#!/usr/bin/env python3
"""Preuve des tweaks FX (notes/44) : le delay Tape et la reverb Plate au choix, par pattern.

Le vrai code de l'OS (Unicorn, EMAC exacte) : le delay et la reverb d'origine tournent en entier, avec leur
préparation (0x4005802e), le pas de leurs lignes (0x40057942) et leurs transferts DMA (0x400573fa), l'eDMA étant
modélisé ici (TCD, modulos, chaînes scatter/gather). La référence de Tape et de Plate est tools/machines/fx/fx.c
compilé pour l'ordinateur (tools/emu/fx_ref.c, l'arithmétique de emac.py).

  1. accroches : les quatre écritures, les instructions remplacées rejouées par fx_hooks.S ; le démarrage par la
     vraie chaîne des crochets (fx_boot, MACRO, Model-TG) ;
  2. « Original » : firmware avec FX == le même sans FX, bloc par bloc (sorties et toute la mémoire des effets) ;
  3. Tape et Plate == la référence, échantillon par échantillon ; registres, pile, accumulateurs, écritures mémoire ;
  4. la mémoire des effets d'origine se vide pendant que les nôtres tournent ; retour à « Original » sans reste ;
  5. par les enveloppes de Model-TG (fx_a, fx_b, fx_c) et son arrêt des effets au silence ;
  6. interface : Settings + DELAY SEND / REVERB SEND, l'octet du pattern, le popup, tout le reste inchangé ;
  7. coût en instructions par bloc, d'origine et nôtres ;
  8. de bout en bout dans l'étage de sortie de l'OS (0x400567ba) : envois, effets, retours, mix.

    python3 tools/emu/test_fx.py --cycles model-cycles_OS1.13.syx [--with 6ch-usbup] [--no-macro] [--quick]

Durée : ~25 min (~8 min avec --quick). g++ ou gcc pour la référence, m68k-linux-gnu-objdump pour l'EMAC.
"""
import argparse
import pathlib
import struct
import subprocess
import sys
import tempfile

import numpy as np
from unicorn import Uc, UC_ARCH_M68K, UC_MODE_BIG_ENDIAN, UC_HOOK_MEM_UNMAPPED, UC_HOOK_MEM_WRITE, UC_HOOK_MEM_READ, \
    UC_HOOK_CODE
from unicorn import m68k_const as mk

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import build                       # noqa: E402
import emac                        # noqa: E402
import mcengine                    # noqa: E402
import test_sdvintage as T         # noqa: E402

BASE = build.BASE
STOP, STACK = 0x40780000, 0x90010000
STATE = 0x40700000                 # la structure des paramètres lissés (a3 de l'étage de sortie) : zone libre du BSS
PAT = 0x40710000                   # un pattern factice (2 pistes suffisent)
CUR_PAT, TEMPO = 0x40a7887c, 0x40149310
IN, OUT_D, OUT_R = 0x8000b990, 0x8000ba90, 0x8000bb90
FX_INIT, SETUP, DELAY, REVERB, RING_STEP, WRITEBACK = 0x400582c4, 0x4005802e, 0x40057488, 0x400579c4, 0x40057942, \
    0x400573fa
EDMA = 0xfc045000                  # TCD du canal n à EDMA + 32 n
DLY_STAGE_PTR = 0x8000b570
DLY_RING, DLY_RING_LEN = 0x4a400000, 0x400000
REV_MEM = (0x4a340000, 0x4a3d8000)  # les 9 lignes de 64 Ko puis les 8 de 4 Ko de la reverb d'origine
SRAM_FX = (0x8000a100, 0x8000bc90)  # tout l'état des effets d'origine en SRAM (TCD, tampons, filtres, sorties)
REGS = [getattr(mk, f"UC_M68K_REG_D{i}") for i in range(2, 8)] + [getattr(mk, f"UC_M68K_REG_A{i}") for i in range(2, 7)]
TRK, CFG = 722, 512
QUIET = 1 << 10                    # -126 dB : le reste des filtres en virgule fixe (quelques LSB), jamais plus au silence

FAILS = []


def check(ok, msg):
    print(("  ok    " if ok else "  FAIL  ") + msg, flush=True)
    if not ok:
        FAILS.append(msg)


class Fx:
    """Les effets de l'OS, fonction par fonction. blob : (adresse, octets) du code de FX tel que son crochet de
    démarrage le recopie (vérifié par boot())."""

    def __init__(self, img, blob=None):
        self.img = bytes(img)
        uc = self.uc = Uc(UC_ARCH_M68K, UC_MODE_BIG_ENDIAN)
        uc.ctl_set_cpu_model(mk.UC_CPU_M68K_ANY)
        uc.mem_map(0x40000000, 0x02400000)     # image, BSS
        uc.mem_map(0x46700000, 0x00100000)     # dernier Mo de la zone de Model-TG : nos mémoires
        uc.mem_map(0x4a000000, 0x00800000)     # lignes de la reverb et du delay d'origine
        uc.mem_map(0x80000000, 0x00020000)     # SRAM
        uc.mem_map(0x90000000, 0x04000000)     # pile, objets factices
        uc.mem_map(0xfc040000, 0x00010000)     # eDMA
        uc.mem_write(BASE, self.img)
        uc.mem_write(STOP, b"\x4e\x71\x4e\x71")
        self.unmapped = []
        uc.hook_add(UC_HOOK_MEM_UNMAPPED,
                    lambda u, a, addr, s, v, d: self.unmapped.append((u.reg_read(mk.UC_M68K_REG_PC), addr)) or False)
        self.emac = emac.EMAC(uc)
        self.emac.install(mcengine._emac_instrs(self.img, [mcengine.CODE_RANGE]))
        if blob:
            uc.mem_write(blob[0], blob[1])
            with tempfile.NamedTemporaryFile(suffix=".bin") as f:
                f.write(blob[1])
                f.flush()
                self.emac.install(emac.disasm(f.name, blob[0], blob[0], blob[0] + len(blob[1])))
        uc.mem_write(0x80000000, self.img[0x4019b590 - BASE:0x401a2a50 - BASE])   # copie du démarrage (0x4000045c)
        uc.mem_write(0x80008000, self.img[0x401a2a50 - BASE:0x401aa140 - BASE])
        uc.hook_add(UC_HOOK_MEM_WRITE, self._dma_write, begin=EDMA, end=EDMA + 0x7ff)
        uc.hook_add(UC_HOOK_MEM_READ, self._dma_read, begin=EDMA, end=EDMA + 0x7ff)
        self.pending, self.dma = None, []
        self.hooks, self.calls = {}, []
        uc.mem_write(TEMPO, struct.pack(">i", 14400))
        uc.mem_write(CUR_PAT, struct.pack(">I", PAT))
        self.call(FX_INIT)                     # l'initialisation des effets au démarrage

    # --- eDMA : un START (CSR bit 0) lance le transfert et sa chaîne (ESG, CSR bit 4). Il est exécuté d'un coup, à
    # la première lecture d'un registre de l'eDMA (l'attente de l'OS, 0x4005697e) ou au retour de la fonction : la
    # valeur écrite n'est pas encore en mémoire quand Unicorn nous prévient. ---
    def _dma_write(self, uc, access, addr, size, value, ud):
        if (addr - EDMA) & 31 == 0x1e and size == 2 and value & 1:
            self.pending = addr - 0x1e

    def _dma_read(self, uc, access, addr, size, value, ud):
        if self.pending is not None:
            tcd, self.pending = self.pending, None
            self._run_dma(tcd)

    def _run_dma(self, tcd):
        uc = self.uc
        raw = bytearray(uc.mem_read(tcd, 32))
        for _ in range(64):
            saddr, attr, _soff, nbytes, _slast, daddr, citer, _doff, sga, _biter, csr = struct.unpack(">IHhIiIHhIHH", raw)
            smod, dmod = attr >> 11, (attr >> 3) & 31
            total = (citer & 0x1ff if citer & 0x8000 else citer & 0x7fff) * nbytes

            def spans(addr, mod, n):
                if not mod:
                    return [(addr, n)]
                mask = (1 << mod) - 1
                base, out = addr & ~mask, []
                while n:
                    addr = base | (addr & mask)
                    k = min(n, base + mask + 1 - addr)
                    out.append((addr, k))
                    addr += k
                    n -= k
                return out
            data = b"".join(bytes(uc.mem_read(a, k)) for a, k in spans(saddr, smod, total))
            pos = 0
            for a, k in spans(daddr, dmod, total):
                uc.mem_write(a, data[pos:pos + k])
                pos += k
            self.dma.append((tcd, saddr, daddr, total))
            if not csr & 0x10:
                break
            raw = bytearray(uc.mem_read(sga, 32))
            uc.mem_write(tcd, bytes(raw))
            if not raw[0x1f] & 1:
                break
        else:
            raise RuntimeError("chaîne DMA sans fin")
        raw[0x1e:0x20] = struct.pack(">H", struct.unpack(">H", raw[0x1e:0x20])[0] & ~0x51 | 0x80)   # fini (DONE)
        uc.mem_write(tcd, bytes(raw))

    def hook(self, addr, ret=0, name=None):
        """Fonction interceptée : ses arguments sont notés, elle rend ret."""
        if addr not in self.hooks:
            self.uc.hook_add(UC_HOOK_CODE, self._hooked, begin=addr, end=addr)
        self.hooks[addr] = (name or f"{addr:#x}", ret)

    def _hooked(self, uc, addr, size, ud):
        sp = uc.reg_read(mk.UC_M68K_REG_A7)
        name, ret = self.hooks[addr]
        self.calls.append((name, struct.unpack(">4I", uc.mem_read(sp + 4, 16))))
        uc.reg_write(mk.UC_M68K_REG_D0, ret(self) if callable(ret) else ret)
        uc.reg_write(mk.UC_M68K_REG_PC, struct.unpack(">I", uc.mem_read(sp, 4))[0])
        uc.reg_write(mk.UC_M68K_REG_A7, sp + 4)

    def call(self, fn, *args, count=60_000_000):
        sp = STACK - 0x400
        self.uc.mem_write(sp, struct.pack(">I" + "I" * len(args), STOP, *[a & 0xffffffff for a in args]))
        self.uc.reg_write(mk.UC_M68K_REG_A7, sp)
        self.uc.emu_start(fn, STOP, count=count)
        if self.uc.reg_read(mk.UC_M68K_REG_PC) != STOP:
            raise RuntimeError(f"{fn:#x} ne revient pas (pc {self.uc.reg_read(mk.UC_M68K_REG_PC):#x})")
        if self.pending is not None:
            tcd, self.pending = self.pending, None
            self._run_dma(tcd)
        return self.uc.reg_read(mk.UC_M68K_REG_D0)

    def u32(self, a):
        return struct.unpack(">I", self.uc.mem_read(a, 4))[0]

    def params(self, time=23, fdbk=32, size=42, tone=64):
        self.uc.mem_write(STATE + 428, struct.pack(">4h", *(int(round(v * 256)) for v in (time, fdbk, size, tone))))

    def select(self, d, r, low=0):
        self.uc.mem_write(PAT + CFG, bytes([(d << 5) | low]))
        self.uc.mem_write(PAT + TRK + CFG, bytes([(r << 5) | low]))

    def out(self):
        return (np.frombuffer(bytes(self.uc.mem_read(OUT_D, 256)), dtype=">i4").reshape(32, 2).astype(np.int64),
                np.frombuffer(bytes(self.uc.mem_read(OUT_R, 256)), dtype=">i4").reshape(32, 2).astype(np.int64))

    def block(self, x, tg=None):
        """Un bloc d'effets comme l'étage de sortie : préparation, delay, reverb, pas des lignes et DMA de retour.
        tg : les symboles de Model-TG, pour passer par ses enveloppes (fx_a, fx_b, fx_c) comme le firmware."""
        x = np.asarray(x, dtype=">i4")
        self.uc.mem_write(IN, x.tobytes())
        if tg:
            # les 6 canaux des pistes (0x80001858), que fx_a écoute : l'entrée sur la piste 1, les autres muettes
            self.uc.mem_write(0x80001858, x[:, 0].tobytes() + bytes(5 * 128))
            self.uc.mem_write(tg["fx_setup"], bytes(4))            # fx_early : ici la préparation est faite par fx_a
            self.call(tg["fx_a"], OUT_D, IN, STATE + 428)
            self.call(tg["fx_b"], OUT_R, IN, STATE + 432)
            self.call(tg["fx_c"])
        else:
            self.call(SETUP, STATE)
            self.call(DELAY, OUT_D, IN, STATE + 428)
            self.call(REVERB, OUT_R, IN, STATE + 432)
            self.call(RING_STEP)
            self.call(WRITEBACK)
        return self.out()

    def run(self, x, tg=None, each=None):
        n = len(x) // 32
        d, r = np.zeros((n * 32, 2), dtype=np.int64), np.zeros((n * 32, 2), dtype=np.int64)
        for b in range(n):
            if each:
                each(self, b)
            d[b * 32:(b + 1) * 32], r[b * 32:(b + 1) * 32] = self.block(x[b * 32:(b + 1) * 32], tg)
        return d, r

    def fx_memory(self):
        """Tout ce que les effets d'origine gardent : SRAM, lignes de la reverb, mémoire du delay."""
        m = self.uc.mem_read
        return bytes(m(SRAM_FX[0], SRAM_FX[1] - SRAM_FX[0])), bytes(m(REV_MEM[0], REV_MEM[1] - REV_MEM[0])), \
            bytes(m(DLY_RING, DLY_RING_LEN))

    def count(self):
        """Compteur d'instructions (lent)."""
        self.n = 0

        def hk(uc, a, s, u):
            self.n += 1
        self.uc.hook_add(UC_HOOK_CODE, hk)


class Ref:
    """fx.c compilé pour l'ordinateur (fx_ref.c), piloté bloc par bloc."""

    def __init__(self, tmp):
        exe = pathlib.Path(tmp) / "fx_ref"
        if not exe.exists():
            subprocess.run(["gcc", "-O2", "-o", str(exe), str(HERE / "fx_ref.c")], check=True)
        self.p = subprocess.Popen([str(exe)], stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    def block(self, d, r, p1, p2, tempo, x):
        self.p.stdin.write(struct.pack("<5i", d, r, p1, p2, tempo) + np.asarray(x, dtype="<i4").tobytes())
        self.p.stdin.flush()
        o = np.frombuffer(self.p.stdout.read(512), dtype="<i4").astype(np.int64)
        return o[:64].reshape(32, 2), o[64:].reshape(32, 2)

    def close(self):
        self.p.stdin.close()
        self.p.wait()


def signal(n_blocks, seed, level_db=-12.0):
    """Entrée d'essai : salves de bruit décorrélé, une note tenue, des silences, un passage à pleine échelle."""
    rng = np.random.default_rng(seed)
    n = n_blocks * 32
    x = np.zeros((n, 2))
    t = np.arange(n)
    amp = 10 ** (level_db / 20)
    seg = n // 8
    x[:seg] = rng.standard_normal((seg, 2)) * amp
    x[2 * seg:3 * seg, 0] = amp * np.sin(2 * np.pi * 220 * t[:seg] / 48000)
    x[2 * seg:3 * seg, 1] = amp * np.sin(2 * np.pi * 331 * t[:seg] / 48000)
    x[4 * seg:4 * seg + seg // 2] = rng.uniform(-1, 1, (seg // 2, 2))        # pleine échelle
    x[6 * seg:6 * seg + 64] = 0.5
    return np.clip(x * 2 ** 31, -2 ** 31, 2 ** 31 - 1).astype(np.int64)


def db(a):
    return 10 * np.log10(np.mean(np.asarray(a, dtype=float) ** 2) + 1e-30) - 20 * np.log10(2 ** 31)


# --- 1. accroches et démarrage -------------------------------------------------------------------------------
BOOT_CALL, BOOT_RESUME = 0x40000530, 0x4000053a
BSS_CLEAR = (0x4019b590, 0x423380b0)       # ce que l'OS, puis Model-TG, remettent à zéro au démarrage


def hooks(base, fx, tweak, others):
    syms = {k: int(v, 16) for k, v in tweak["symbols"].items()}
    at, size = int(tweak["append"]["at"], 16), tweak["append"]["size"]
    dst, n = syms["fx_dst"], syms["fx_size"]
    code = fx[syms["fx_blob"] - BASE:syms["fx_blob"] - BASE + n]
    check(syms["fx_blob"] + n == at + size == BASE + len(fx) and at == BASE + len(base),
          f"après l'image : crochet de démarrage en {at:#x}, puis {n} o de code pour {dst:#x}")
    for va, sym, op, k in ((DELAY, "fx_delay_entry", 0x4ef9, 8), (REVERB, "fx_reverb_entry", 0x4ef9, 8),
                           (0x400081ce, "fx_enc_hook", 0x4eb9, 6)):
        want = struct.pack(">HI", op, syms[sym]) + b"\x4e\x71" * ((k - 6) // 2)
        check(fx[va - BASE:va - BASE + k] == want, f"{va:#x} : {'jmp' if op == 0x4ef9 else 'jsr'} {sym} ({syms[sym]:#x})")
        stub = code[syms[sym] - dst:]
        if k == 8:                             # les 8 octets remplacés sont rejoués, puis jmp à l'instruction suivante
            moved = base[va - BASE:va - BASE + 8]
            back = struct.pack(">HI", 0x4ef9, va + 8)
            i = stub.find(moved)
            check(0 <= i < 32 and stub[i + 8:i + 14] == back,
                  f"  {sym} rejoue {moved.hex()} puis jmp {va + 8:#x}")
    prev = struct.unpack(">I", base[BOOT_CALL + 2 - BASE:BOOT_CALL + 6 - BASE])[0]
    check(fx[BOOT_CALL - BASE:BOOT_CALL + 6 - BASE] == struct.pack(">HI", 0x4eb9, at) and prev == syms["fx_prev"] and
          base[BOOT_CALL - BASE:BOOT_CALL + 2 - BASE] == b"\x4e\xb9",
          f"{BOOT_CALL:#x} : jsr fx_boot ({at:#x}), à la place du crochet précédent ({prev:#x})")
    inside = [f"{a:#x}" for va in (DELAY, REVERB) for a in range(va + 1, va + 8) if struct.pack(">I", a) in fx]
    callers = {va: [m for m in range(0, len(fx) - 5, 2) if fx[m + 2:m + 6] == struct.pack(">I", va) and
                    fx[m:m + 2] in (b"\x4e\xb9", b"\x4e\xf9")] for va in (DELAY, REVERB)}
    check(not inside and all(len(c) == 1 for c in callers.values()),
          "les 8 octets remplacés ne sont visés par aucune adresse de l'image ; un seul appel pour chaque fonction "
          f"({', '.join(f'{c[0] + BASE:#x}' for c in callers.values() if c)})")
    diff = [i for i in range(len(base)) if base[i] != fx[i]]
    spans = sorted({(DELAY, 8), (REVERB, 8), (0x400081ce, 6), (BOOT_CALL + 2, 4)})
    ok = all(any(va - BASE <= i < va - BASE + k for va, k in spans) for i in diff) and fx[:len(base)] != base
    check(ok and len(fx) == len(base) + size, f"rien d'autre ne change dans l'image : {len(diff)} octets, {size} o ajoutés à {at:#x}")
    mine = [(w["off"], w["off"] + len(w["new"]) // 2) for w in tweak["writes"]]
    shared = ((0x400081ce, 0x400081d4), (BOOT_CALL, BOOT_CALL + 6))        # les deux appels repris du tweak précédent
    clash = []
    for t in others:
        if t["id"] in tweak["conflicts"] or t["id"] == tweak["id"]:
            continue
        for w in t["writes"]:
            a, b = w["off"], w["off"] + len(w["new"]) // 2
            if any(a < y and x < b for x, y in mine) and not any(lo - BASE <= a and b <= hi - BASE for lo, hi in shared):
                clash.append((t["id"], hex(w["off"] + BASE)))
    check(not clash, "aucune écriture commune avec un autre tweak (hors les deux appels repris : encodeurs en 0x400081ce, "
          f"crochet de démarrage en {BOOT_CALL:#x}) {clash[:3]}")
    return syms, (dst, code)


def boot(base, fx, syms, code, tweaks, stock, ids):
    """Le démarrage, par le vrai code : jsr de 0x40000530 -> fx_boot -> (crochet de MACRO) -> boot_extra_hook de
    Model-TG, qui remet le BSS à zéro (0x4019b590..0x423380b0 sauf son bloc) et reprend en 0x4000053a."""
    tg = {k: int(v, 16) for k, v in tweaks["model-tg-st"]["symbols"].items()}
    res = {}
    for name, img in (("sans FX", base), ("avec FX", fx)):
        uc = Uc(UC_ARCH_M68K, UC_MODE_BIG_ENDIAN)
        uc.ctl_set_cpu_model(mk.UC_CPU_M68K_ANY)
        uc.mem_map(0x40000000, 0x02400000)
        uc.mem_map(0x46700000, 0x00100000)
        uc.mem_map(0x90000000, 0x00100000)
        uc.mem_write(0x40000000, b"\xa5" * 0x02400000)        # la RAM à l'allumage : n'importe quoi
        uc.mem_write(0x46700000, b"\xa5" * 0x00100000)
        uc.mem_write(BASE, img)
        # boot_extra_hook de Model-TG commence par régler ACR1 (movec, qu'Unicorn ne connaît pas) : sauté ici
        i = img.find(b"\x4e\x7b", tg["boot_extra_hook"] - BASE, tg["boot_extra_hook"] - BASE + 32)
        uc.mem_write(BASE + i, b"\x4e\x71\x4e\x71")
        bad = []
        uc.hook_add(UC_HOOK_MEM_UNMAPPED, lambda u, a, addr, s_, v, d: bad.append(addr) or False)
        for i, r in enumerate(REGS):
            uc.reg_write(r, 0x5a5a0000 + i)
        sp = 0x90080000
        uc.reg_write(mk.UC_M68K_REG_A7, sp)
        try:
            uc.emu_start(BOOT_CALL, BOOT_RESUME, count=400_000_000)
        except Exception as ex:
            bad.append(str(ex))
        kept = all(uc.reg_read(r) == 0x5a5a0000 + i for i, r in enumerate(REGS))
        res[name] = (uc, uc.reg_read(mk.UC_M68K_REG_PC), kept, bad, uc.reg_read(mk.UC_M68K_REG_A7))
    (ua, pca, ka, bada, spa), (ub, pcb, kb, badb, spb) = res["sans FX"], res["avec FX"]
    check(pca == pcb == BOOT_RESUME and ka and kb and not bada and not badb and spa == spb,
          f"démarrage : la chaîne des crochets reprend en {BOOT_RESUME:#x}, d2-d7/a2-a6 et la pile comme sans FX {badb[:2]}")
    dst, blob = code
    got = bytes(ub.mem_read(dst, len(blob)))
    after = bytes(ub.mem_read(dst + len(blob), 0x46800000 - dst - len(blob)))
    check(got == blob and after == b"\xa5" * len(after) and bytes(ua.mem_read(dst, 64)) == b"\xa5" * 64,
          f"fx_boot recopie les {len(blob)} o du code à {dst:#x}, et rien de plus (le reste du Mo est intact)")
    lo, hi = tg["blob_start"], tg["reserved_end"]
    at = syms["fx_boot"]
    tail = bytes(ub.mem_read(hi, BASE + len(fx) - hi))
    blob_tg = bytearray(ub.mem_read(lo, hi - lo))
    i = fx.find(b"\x4e\x7b", tg["boot_extra_hook"] - BASE, tg["boot_extra_hook"] - BASE + 32)
    blob_tg[BASE + i - lo:BASE + i - lo + 4] = fx[i:i + 4]          # le movec sauté ci-dessus
    check(not any(tail) and bytes(blob_tg) == fx[lo - BASE:hi - BASE],
          f"puis Model-TG remet à zéro tout ce qui suit son bloc ({hi:#x}.., crochet et copie de FX compris) : "
          "le code ne pouvait pas rester après l'image")
    # tout le reste de la mémoire : comme sans FX
    ma, mb = bytearray(ua.mem_read(BASE, BSS_CLEAR[1] - BASE)), bytearray(ub.mem_read(BASE, BSS_CLEAR[1] - BASE))
    for va, k in ((DELAY, 8), (REVERB, 8), (0x400081ce, 6), (BOOT_CALL + 2, 4)):
        mb[va - BASE:va - BASE + k] = ma[va - BASE:va - BASE + k]
    low = bytes(ua.mem_read(0x46700000, dst - 0x46700000)) == bytes(ub.mem_read(0x46700000, dst - 0x46700000))
    check(ma == mb and low, "toute la mémoire après le démarrage (image, BSS, charge utile de MACRO) identique au "
          "firmware sans FX, hors nos 4 écritures et notre code")
    if "macro-tg" in ids:
        want = build.payload_runtime(tweaks["macro-tg"], stock, None)
        pd = int(tweaks["macro-tg"]["append"]["dest"], 16)
        check(bytes(ub.mem_read(pd, len(want))) == want, f"la charge utile de MACRO est reconstituée à {pd:#x} comme sans FX")


# --- 2. « Original » -----------------------------------------------------------------------------------------
def original(base, fx, code, nblocks):
    x = signal(nblocks, 1)
    for label, setup in (("octets du pattern à 0", lambda e: e.select(0, 0)),
                         ("bits 0-4 pris (arpégiateur), 5-7 à 0", lambda e: e.select(0, 0, low=0x1f)),
                         ("valeurs hors limites (5 et 7)", lambda e: e.select(5, 7)),
                         ("pas de pattern (pointeur nul)", lambda e: e.uc.mem_write(CUR_PAT, bytes(4)))):
        a, b = Fx(base), Fx(fx, code)
        setup(b)

        def each(e, k):
            e.params(time=3 + (k // 40) % 5, fdbk=20 + k % 90, size=10 + k % 110, tone=(k * 3) % 128)
        da, ra = a.run(x, each=each)
        db_, rb = b.run(x, each=each)
        same = np.array_equal(da, db_) and np.array_equal(ra, rb)
        mem = a.fx_memory() == b.fx_memory()
        check(same and mem and not a.unmapped and not b.unmapped and np.abs(da).max() > 2 ** 24 and np.abs(ra).max() > 2 ** 22,
              f"Original, {label} : {nblocks} blocs identiques au firmware sans FX (delay {db(da):.1f} dB, reverb "
              f"{db(ra):.1f} dB), mémoire des effets identique")


# --- 3. Tape et Plate == la référence ------------------------------------------------------------------------
def guarded_call(e, fn, args, allowed):
    """Appel avec des sentinelles dans d2-d7/a2-a6 ; écritures mémoire notées. Rend (registres gardés, écritures
    hors des zones permises)."""
    uc = e.uc
    for i, r in enumerate(REGS):
        uc.reg_write(r, 0x5a5a0000 + i)
    stray = []

    def wr(u, access, addr, size, value, ud):
        if not any(lo <= addr and addr + size <= hi for lo, hi in allowed):
            stray.append((u.reg_read(mk.UC_M68K_REG_PC), addr))
    h = uc.hook_add(UC_HOOK_MEM_WRITE, wr)
    e.call(fn, *args)
    uc.hook_del(h)
    kept = all(uc.reg_read(r) == 0x5a5a0000 + i for i, r in enumerate(REGS))
    return kept and uc.reg_read(mk.UC_M68K_REG_A7) == STACK - 0x400 + 4, stray


def versus_ref(fx, code, syms, nblocks, tmp):
    st, ring, pbuf = syms["st"], syms["ring"], syms["pbuf"]
    stack = (STACK - 0x1000, STACK)
    for name, sel, fn, out_addr, prm, mem in (
            ("Tape", (1, 0), DELAY, OUT_D, STATE + 428, (ring, ring + 4 * 131072)),
            ("Plate", (0, 1), REVERB, OUT_R, STATE + 432, (pbuf, pbuf + 4 * 16384))):
        e, ref = Fx(fx, code), Ref(tmp)
        e.select(*sel)
        x = signal(nblocks, 2 if name == "Tape" else 3)
        ok = kept = True
        strays, worst, peak, accs = [], 0, 0, True
        stage = e.u32(DLY_STAGE_PTR)
        allowed = [(out_addr, out_addr + 256), (st, st + 256), mem, stack]
        # et l'état de l'effet d'origine qu'il remplace : son tampon de transfert, ses filtres
        allowed += [(stage, stage + 256), (0x8000b8dc, 0x8000b8ec)] if name == "Tape" else \
            [(0x8000a9b0, 0x8000b4e0), (0x8000a4c8, 0x8000a4d0), (0x8000a47c, 0x8000a4bc), (0x8000a1d0, 0x8000a3d0)]
        for k in range(nblocks):
            p1 = (1 + (k // 60) % 7) * 256 + (k % 256 if k > nblocks // 2 else 0)      # Time / Size, avec fractions
            p2 = (10 + (k * 7) % 118) * 256 if name == "Tape" else ((k * 5) % 128) * 256 + 77
            if name == "Plate":
                p1 = ((k // 30) * 17 % 128) * 256 + 3
            tempo = 14400 if k < nblocks * 2 // 3 else 9000
            blk = x[k * 32:(k + 1) * 32]
            e.uc.mem_write(IN, blk.astype(">i4").tobytes())
            e.uc.mem_write(prm, struct.pack(">2h", p1, p2))
            e.uc.mem_write(TEMPO, struct.pack(">i", tempo))
            e.call(SETUP, STATE)
            if name == "Plate":                    # le delay d'abord, comme dans le bloc : il lit le choix du pattern
                e.call(DELAY, OUT_D, IN, STATE + 428)
            k_, s_ = guarded_call(e, fn, (out_addr, IN, prm), allowed)
            kept &= k_
            strays += s_
            accs &= all(a == 0 for a in e.emac.acc)
            got = e.out()[0 if name == "Tape" else 1]
            want = ref.block(sel[0], sel[1], p1, p2, tempo, blk)[0 if name == "Tape" else 1]
            if not np.array_equal(got, want):
                ok = False
                worst = max(worst, int(np.abs(got - want).max()))
            peak = max(peak, int(np.abs(got).max()))
            e.call(RING_STEP)
            e.call(WRITEBACK)
        ref.close()
        check(ok and peak > 2 ** 26, f"{name} == fx.c compilé pour l'ordinateur, {nblocks} blocs, échantillon par échantillon "
              f"(crête {20 * np.log10(peak / 2 ** 31):.1f} dB{'' if ok else f', écart {worst}'})")
        check(kept and accs and not e.unmapped, f"{name} : d2-d7/a2-a6 et la pile gardés, accumulateurs vides au retour, "
              "aucun accès hors mémoire")
        check(not strays, f"{name} : n'écrit que sa sortie, son état, sa mémoire et le tampon de l'effet d'origine "
              f"{[(hex(a), hex(b)) for a, b in strays[:3]]}")


# --- 4. la mémoire des effets d'origine ----------------------------------------------------------------------
def flush(fx, code, quick):
    e = Fx(fx, code)
    e.params(time=1, fdbk=100, size=110, tone=64)
    x = signal(96, 4, level_db=-6)
    e.run(x)                                       # Original : le delay et la reverb d'origine se remplissent
    _, rev, dly = e.fx_memory()
    check(any(rev) and any(dly), "avant : les lignes de la reverb et la mémoire du delay d'origine contiennent du son")
    e.select(1, 1)
    silence = np.zeros((32, 2), dtype=np.int64)
    n = 600
    wrote_zero = True
    for k in range(n):
        e.block(x[(k % 96) * 32:(k % 96 + 1) * 32])
        w = (e.u32(0x8000b988) - 32) & 0x7ffff     # là où le DMA de retour vient d'écrire le delay
        wrote_zero &= not any(e.uc.mem_read(DLY_RING + 8 * w, 256))
    _, rev, _ = e.fx_memory()
    check(wrote_zero, f"Tape : pendant {n} blocs, le DMA de retour n'écrit que du silence dans la mémoire du delay d'origine")
    check(not any(rev), f"Plate : après {n} blocs, les 17 lignes de la reverb d'origine sont vides")
    # retour à Original : la reverb d'origine repart du silence (le delay se vide en un tour de sa mémoire, 10,9 s)
    e.select(0, 0)
    e.uc.mem_write(DLY_RING, bytes(DLY_RING_LEN))  # ce que 16 384 blocs de Tape auraient fait (vérifié ci-dessus)
    peak = 0
    for k in range(40):
        d, r = e.block(silence)
        if k >= 8:                                 # le temps que les filtres de sortie de la reverb se vident
            peak = max(peak, int(np.abs(r).max()), int(np.abs(d).max()))
    check(peak < 2 ** 12, f"retour à Original sans reste : sorties sous -114 dB après 8 blocs de silence (crête {peak})")
    if quick:
        return
    # Tape puis Original puis Tape : la bande repart vide
    e = Fx(fx, code)
    e.select(1, 1)
    e.params(time=0, fdbk=120, size=120, tone=64)
    e.run(x)
    e.select(0, 0)
    e.run(x[:320])
    e.select(1, 1)
    d, r = e.run(np.zeros((64 * 32, 2), dtype=np.int64))
    check(max(np.abs(d).max(), np.abs(r).max()) < QUIET, "Tape et Plate choisis de nouveau : ils repartent vides (64 blocs "
          "de silence, sorties sous -126 dB)")


# --- 5. par les enveloppes de Model-TG -----------------------------------------------------------------------
def through_tg(fx, code, tg, nblocks, tmp):
    x = signal(nblocks, 5)
    e, ref = Fx(fx, code), Ref(tmp)
    e.select(1, 1)
    e.params(time=2, fdbk=70, size=80, tone=90)
    ok = True
    for k in range(nblocks):
        blk = x[k * 32:(k + 1) * 32]
        d, r = e.block(blk, tg)
        # la référence reçoit les mêmes paramètres pour les deux effets : deux appels
        wd = ref.block(1, 1, 2 * 256, 70 * 256, 14400, blk)[0]
        ok &= np.array_equal(d, wd)
    ref.close()
    check(ok and not e.unmapped, f"par fx_a / fx_b / fx_c de Model-TG : Tape identique à la référence, {nblocks} blocs")
    # l'arrêt des effets au silence : FX_HOLD blocs sans son en entrée ni en sortie
    e = Fx(fx, code)
    e.select(1, 1)
    e.params(time=0, fdbk=20, size=0, tone=64)
    e.run(signal(16, 6), tg)
    silence = np.zeros((32, 2), dtype=np.int64)
    n = 0
    while n < 6000 and max(np.abs(e.block(silence, tg)[0]).max(), np.abs(e.out()[1]).max()) >= QUIET:
        n += 1
    check(n < 6000, f"au silence, les sorties de Tape et de Plate passent sous -126 dB en {n} blocs (le seuil de "
          "Model-TG pour éteindre les effets : -90 dB)")
    e.uc.mem_write(tg["fx_cnt"], struct.pack(">I", 16384 - 3))
    for _ in range(6):
        e.block(silence, tg)
    off = e.u32(tg["fx_off"])
    e.count()
    e.n = 0
    d, r = e.block(silence, tg)
    asleep = e.n
    check(off == 1 and not d.any() and not r.any() and asleep < 1500,
          f"Model-TG éteint les effets après 16 384 blocs de silence : sorties nulles, {asleep} instructions par bloc")
    d, r = e.block(signal(1, 7), tg)
    check(e.u32(tg["fx_off"]) == 0, "au premier son, ils repartent")


# --- 6. interface --------------------------------------------------------------------------------------------
APP, PATOBJ, EVENT, VT, TEXT = 0x93a00000, 0x93900000, 0x93b00000, 0x93c00000, 0x93d00000


def ui(fx, code, syms, tg):
    def machine(separate):
        e = Fx(fx, code)
        e.hook(tg["gm_enc_gate"], 0x77, "gm_enc_gate")
        e.hook(tg["show_popup"], 0, "popup")
        e.hook(0x400cf866, APP, "app")
        e.hook(0x4000f208, PATOBJ, "pattern")
        e.hook(VT + 0x100, 0, "notify")
        # deux objets « piste » (0x4000cfcc : objet du pattern + 0x70 + 88 t) ; vtable[10] = 0x400d639e (le pointeur en
        # +16), vtable[4] = la notification
        e.uc.mem_write(VT, struct.pack(">11I", *([0] * 4 + [VT + 0x100] + [0] * 5 + [0x400d639e])))
        for t in range(2):
            obj = PATOBJ + 0x70 + 88 * t
            data = (0x93e00000 + 0x1000 * t) if separate else PAT + TRK * t
            e.uc.mem_write(obj, struct.pack(">I", VT))
            e.uc.mem_write(obj + 16, struct.pack(">I", data))
        return e

    def turn(e, enc, delta, held=1):
        e.calls.clear()
        e.uc.mem_write(tg["set_held"], struct.pack(">I", held))
        e.uc.mem_write(EVENT, struct.pack(">IIIIiB", 0, 0, 0, enc, delta, 0))
        for i, r in enumerate(REGS):
            e.uc.reg_write(r, 0x5a5a0000 + i)
        d0 = e.call(syms["fx_enc_hook"], 0x1234, EVENT)
        kept = all(e.uc.reg_read(r) == 0x5a5a0000 + i for i, r in enumerate(REGS)) and \
            e.uc.reg_read(mk.UC_M68K_REG_A7) == STACK - 0x400 + 4
        return d0, kept

    def popups(e):
        out = []
        for name, args in e.calls:
            if name == "popup":
                s = bytes(e.uc.mem_read(args[0], 32))
                out.append(s[:s.index(0)].decode())
        return out

    e = machine(False)
    e.select(0, 0, low=0x1b)                        # bits de l'arpégiateur : à garder
    ok = True
    for enc, held in ((8, 0), (9, 0), (1, 1), (7, 1), (10, 1), (2, 1), (15, 1)):
        d0, kept = turn(e, enc, 1, held)
        ok &= d0 == 0x77 and kept and e.calls == [("gm_enc_gate", (0x1234, EVENT, e.calls[0][1][2], e.calls[0][1][3]))]
    cfg = bytes(e.uc.mem_read(PAT + CFG, 1)) + bytes(e.uc.mem_read(PAT + TRK + CFG, 1))
    check(ok and cfg == b"\x1b\x1b" and e.u32(tg["mod_used"]) == 0,
          "sans Settings, ou avec un autre encodeur (1, 2, 7, 10, 15) : l'événement va à gm_enc_gate comme avant, rien ne change")

    for separate in (False, True):
        e = machine(separate)
        e.select(0, 0, low=0x1b)
        if separate:
            for t in range(2):
                e.uc.mem_write(0x93e00000 + 0x1000 * t + CFG, b"\x1b")
        log = []
        d0, kept = turn(e, 8, 1)
        log.append((d0, kept, popups(e), e.u32(tg["mod_used"]), bytes(e.uc.mem_read(PAT + CFG, 1))))
        ok = log[0] == (1, True, ["Delay FX\nOriginal"], 1, b"\x1b") and not any(n == "gm_enc_gate" for n, _ in e.calls)
        for _ in range(2):
            turn(e, 8, 1)
            ok &= popups(e) == [] and bytes(e.uc.mem_read(PAT + CFG, 1)) == b"\x1b"
        turn(e, 8, 1)                               # 4e cran : Tape
        notify = [a for n, a in e.calls if n == "notify"]
        tag = e.u32(notify[0][1]) if notify else 0
        ok &= popups(e) == ["Delay FX\nTape"] and bytes(e.uc.mem_read(PAT + CFG, 1)) == b"\x3b"
        ok &= len(notify) == 1 and notify[0][0] == PATOBJ + 0x70 and tag == 0x400ff5ac
        if separate:
            ok &= bytes(e.uc.mem_read(0x93e00000 + CFG, 1)) == b"\x3b"
        for _ in range(9):                          # au bout : rien de plus
            turn(e, 8, 1)
            ok &= popups(e) == [] and bytes(e.uc.mem_read(PAT + CFG, 1)) == b"\x3b"
        turn(e, 9, 2)                               # l'autre potard : montre son choix, puis change au 4e cran
        ok &= popups(e) == ["Reverb FX\nOriginal"]
        turn(e, 9, 2)
        ok &= popups(e) == ["Reverb FX\nPlate"] and bytes(e.uc.mem_read(PAT + TRK + CFG, 1)) == b"\x3b"
        ok &= [a[0] for n, a in e.calls if n == "notify"] == [PATOBJ + 0x70 + 88]
        turn(e, 8, -3)
        ok &= popups(e) == ["Delay FX\nTape"]       # retour sur ce potard : son choix
        turn(e, 8, -3)
        ok &= popups(e) == ["Delay FX\nOriginal"] and bytes(e.uc.mem_read(PAT + CFG, 1)) == b"\x1b"
        ok &= bytes(e.uc.mem_read(PAT + TRK + CFG, 1)) == b"\x3b" and not e.unmapped
        check(ok, "Settings + DELAY SEND (8) / REVERB SEND (9) : 1er cran = le choix en cours, 4 crans = le suivant, bits "
              "0-4 gardés, popup, notification de la piste, mod_used ; " +
              ("données de l'interface à part : écrites aussi" if separate else "données de l'interface = le pattern joué"))
    # de bout en bout : le choix fait par l'interface est celui que prend le côté audio au bloc suivant
    e = machine(False)
    e.select(0, 0)
    for enc in (8, 9):
        for _ in range(5):
            turn(e, enc, 1)
    e.params(time=0, fdbk=0, size=60, tone=64)
    e.run(signal(8, 8))
    st = syms["st"]
    check((e.u32(st), e.u32(st + 4)) == (1, 1), "le choix fait par l'interface est pris par le côté audio (st.sel_d, st.sel_r)")
    e.uc.mem_write(PAT + CFG, b"\x00")
    e.uc.mem_write(PAT + TRK + CFG, b"\x00")
    e.block(np.zeros((32, 2), dtype=np.int64))
    check((e.u32(st), e.u32(st + 4)) == (0, 0), "un pattern sans choix : retour aux effets d'origine au bloc suivant")


# --- 7. coût -------------------------------------------------------------------------------------------------
def cost(base, fx, code):
    x = signal(48, 9, level_db=-9)
    rows = []
    for name, img, extra, sel in (("d'origine", base, None, (0, 0)), ("Original, avec FX", fx, code, (0, 0)),
                                  ("Tape + Plate", fx, code, (1, 1))):
        e = Fx(img, extra)
        e.select(*sel)
        e.params(time=2, fdbk=60, size=64, tone=64)
        e.run(x[:32 * 24])
        e.count()
        dl, rv = [], []
        lines = set()

        def acc(u, access, addr, size, value, ud):
            if addr < 0x80000000:
                lines.add(addr >> 4)
        h = e.uc.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, acc)
        nlines = []
        for k in range(24, 48):
            e.uc.mem_write(IN, x[k * 32:(k + 1) * 32].astype(">i4").tobytes())
            e.call(SETUP, STATE)
            lines.clear()
            e.n = 0
            e.call(DELAY, OUT_D, IN, STATE + 428)
            dl.append(e.n)
            e.n = 0
            e.call(REVERB, OUT_R, IN, STATE + 432)
            rv.append(e.n)
            nlines.append(len(lines))
            e.call(RING_STEP)
            e.call(WRITEBACK)
        e.uc.hook_del(h)
        rows.append((name, max(dl), max(rv), max(nlines)))
    for name, d, r, ln in rows:
        print(f"        {name:18s} delay {d:5d}  reverb {r:5d}  instructions par bloc au pire ; {ln} lignes de 16 o en SDRAM")
    (_, d0, r0, _), (_, d1, r1, _), (_, d2, r2, _) = rows
    check(d1 - d0 < 40 and r1 - r0 < 8, f"Original : {d1 - d0} instructions de plus pour le delay (le choix du pattern), "
          f"{r1 - r0} pour la reverb")
    return rows


# --- 8. dans l'étage de sortie -------------------------------------------------------------------------------
OUT_STAGE, MIX, MIX_DONE = 0x400567ba, 0x8000b990, 0x40056b38


def stage(base, fx, code, syms, tg, nblocks, tmp):
    """L'étage de sortie entier, comme l'appelle la fonction audio (0x40059872) : coefficients des 6 pistes, envois
    vers le delay et la reverb (le delay part aussi dans la reverb), les deux effets par les enveloppes de Model-TG,
    leurs retours dans le mix. MIX_DONE : le mix stéréo est prêt (juste avant l'écrêtage, ou les effets master de
    Model-TG)."""
    x = signal(nblocks, 11, level_db=-14)

    def run(img, extra, sel, probe=None):
        e = Fx(img, extra)
        e.select(*sel)
        e.params(time=1, fdbk=64, size=64, tone=80)
        e.uc.mem_write(STATE, struct.pack(">6h", *([0x6400] * 6)))            # niveau des pistes
        for t in range(6):                                                   # Volume, Delay Send, Reverb Send, Pan
            for slot, v in ((0x13, 100), (0x14, 60 + 10 * t), (0x15, 90 - 10 * t), (0x16, 30 + 15 * t)):
                e.uc.mem_write(STATE + 0xe + 0x42 * t + 2 * slot, struct.pack(">h", v * 256))
        e.uc.mem_write(0x4013e77c, struct.pack(">h", 0x7f00))                # volume général
        snap = []
        # les sorties des effets sont relevées là aussi : l'étage reprend ensuite le tampon du delay (0x40056b68)
        e.uc.hook_add(UC_HOOK_CODE, lambda u, a, s_, d: snap.append(tuple(bytes(u.mem_read(m, 256))
                                                                         for m in (MIX, OUT_D, OUT_R))),
                      begin=MIX_DONE, end=MIX_DONE)
        if probe:
            probe(e)
        outs = []
        for b in range(nblocks):
            blk = x[b * 32:(b + 1) * 32]
            chans = np.zeros((6, 32), dtype=">i4")
            chans[0], chans[3], chans[5] = blk[:, 0], blk[:, 1], blk[:, 0] // 2
            e.uc.mem_write(0x80001858, chans.tobytes())
            e.uc.mem_write(tg["fx_setup"], bytes(4))
            for i, r in enumerate(REGS):
                e.uc.reg_write(r, 0x5a5a0000 + i)
            e.call(OUT_STAGE, 0x4a3ed080, STATE)
            if not all(e.uc.reg_read(r) == 0x5a5a0000 + i for i, r in enumerate(REGS)):
                raise RuntimeError("registres")
            outs.append(snap[-1])
        return e, outs

    a, ref_outs = run(base, None, (0, 0))
    b, outs = run(fx, code, (0, 0))
    mix = np.frombuffer(b"".join(o[0] for o in outs), dtype=">i4").astype(np.int64)
    heard = all(np.abs(np.frombuffer(b"".join(o[k] for o in outs), dtype=">i4").astype(np.int64)).max() > 2 ** 20
                for k in (1, 2))
    check(outs == ref_outs and heard and not a.unmapped and not b.unmapped and np.abs(mix).max() > 2 ** 28,
          f"Original : {nblocks} blocs de l'étage de sortie identiques au firmware sans FX (mix {db(mix):.1f} dB, "
          "sorties du delay et de la reverb)")
    seen = {"tape": [], "plate": []}

    def probe(e):
        def grab(name):
            def f(u, addr, size, d):
                sp = u.reg_read(mk.UC_M68K_REG_A7)
                o, i, p = struct.unpack(">3I", u.mem_read(sp + 4, 12))
                seen[name].append((o, bytes(u.mem_read(i, 256)), struct.unpack(">2h", u.mem_read(p, 4))))
            return f
        e.uc.hook_add(UC_HOOK_CODE, grab("tape"), begin=syms["fx_tape"], end=syms["fx_tape"])
        e.uc.hook_add(UC_HOOK_CODE, grab("plate"), begin=syms["fx_plate"], end=syms["fx_plate"])
    c, outs = run(fx, code, (1, 1), probe)
    rd, rr = Ref(tmp), Ref(tmp)
    ok = len(seen["tape"]) == len(seen["plate"]) == nblocks
    for k in range(min(nblocks, len(seen["tape"]), len(seen["plate"]))):
        (od, ind, pd), (or_, inr, pr) = seen["tape"][k], seen["plate"][k]
        wd = rd.block(1, 0, pd[0], pd[1], 14400, np.frombuffer(ind, dtype=">i4"))[0]
        wr = rr.block(0, 1, pr[0], pr[1], 14400, np.frombuffer(inr, dtype=">i4"))[1]
        ok &= (od, or_) == (OUT_D, OUT_R)
        ok &= np.array_equal(np.frombuffer(outs[k][1], dtype=">i4").reshape(32, 2), wd)
        ok &= np.array_equal(np.frombuffer(outs[k][2], dtype=">i4").reshape(32, 2), wr)
    rd.close()
    rr.close()
    mix2 = np.frombuffer(b"".join(o[0] for o in outs), dtype=">i4").astype(np.int64)
    dly = np.frombuffer(b"".join(o[1] for o in outs), dtype=">i4").astype(np.int64)
    rev = np.frombuffer(b"".join(o[2] for o in outs), dtype=">i4").astype(np.int64)
    check(ok and not c.unmapped and np.abs(dly).max() > 2 ** 24 and np.abs(rev).max() > 2 ** 20,
          f"Tape + Plate dans l'étage de sortie : appelés une fois par bloc avec les tampons de l'OS, "
          f"sorties == la référence sur l'entrée reçue ({nblocks} blocs ; delay {db(dly):.1f} dB, reverb {db(rev):.1f} dB)")
    check(not np.array_equal(mix, mix2) and np.abs(mix2).max() > 2 ** 28,
          f"leurs retours sont dans le mix ({db(mix2):.1f} dB ; Original {db(mix):.1f} dB)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cycles", required=True, help="model-cycles_OS1.13.syx officiel")
    ap.add_argument("--with", dest="extra", default="", help="autres tweaks appliqués avant (6ch-usbup,…)")
    ap.add_argument("--no-macro", action="store_true", help="la version sans MACRO (fx-tg)")
    ap.add_argument("--quick", action="store_true", help="moins de blocs")
    ap.add_argument("--only", default="", help="seulement ces parties (2,5,8 ; la 1 est toujours faite)")
    args = ap.parse_args()
    stock = T.main_os_from_syx(args.cycles)
    dev, tweaks = build.load_catalog()["model-cycles_OS1.13"]
    if build.sha(stock) != dev["section_sha256"]:
        raise SystemExit("!! ce n'est pas le MAIN OS 1.13 officiel")
    ids = [i for i in args.extra.split(",") if i] + ["model-tg-st"] + ([] if args.no_macro else ["macro-tg"])
    tid = "fx-tg" if args.no_macro else "fx-macro-tg"

    def image(names):
        chosen = sorted((tweaks[i] for i in names), key=lambda t: t["order"])
        build.check_conflicts(chosen)
        patched, _ = build.apply_writes(stock, chosen)
        payload, _ = build.build_payload(chosen, stock, None)
        return bytes(patched) + payload
    base, fx = image(ids), image(ids + [tid])
    tweak = tweaks[tid]
    tg = {k: int(v, 16) for k, v in tweaks["model-tg-st"]["symbols"].items()}
    n = 1 if args.quick else 3
    print(f"FX ({tid}) sur {' + '.join(ids)} : MAIN OS {build.sha(fx)[:16]}…")
    print("1. accroches et démarrage")
    syms, code = hooks(base, fx, tweak, [t for t in tweaks.values() if t["id"] in ids])
    boot(base, fx, syms, code, tweaks, stock, ids)
    only = {int(k) for k in args.only.split(",") if k}

    def part(k, title):
        if only and k not in only:
            return False
        print(f"{k}. {title}")
        return True
    if part(2, "Original"):
        original(base, fx, code, 96 * n)
    with tempfile.TemporaryDirectory() as tmp:
        if part(3, "Tape et Plate == la référence"):
            versus_ref(fx, code, syms, 480 * n, tmp)
        if part(4, "mémoire des effets d'origine"):
            flush(fx, code, args.quick)
        if part(5, "par Model-TG"):
            through_tg(fx, code, tg, 160 * n, tmp)
        if part(6, "interface"):
            ui(fx, code, syms, tg)
        if part(7, "coût"):
            cost(base, fx, code)
        if part(8, "dans l'étage de sortie de l'OS"):
            stage(base, fx, code, syms, tg, 64 * n, tmp)
    if only:
        print(f"(parties {sorted(only)} seulement)")
    print("TOUT OK" if not FAILS else f"{len(FAILS)} ÉCHEC(S)")
    raise SystemExit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
