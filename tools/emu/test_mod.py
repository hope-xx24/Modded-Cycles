#!/usr/bin/env python3
"""Preuve de la machine MOD (notes/57) : 32-macro-mod-tg.json, Model-TG + MACRO + MOD, par-dessus 30-model-tg-st.json.
Le vrai code de l'OS, émulé (Unicorn), avec la charge utile telle que le crochet de démarrage la reconstitue.

  1. Écritures : celles de 32-macro-tg.json, plus l'appel de la boucle des voix (0x4005981e) vers mod_vl, qui
     appelle la cible d'avant (le détour fx_early de Model-TG) avec les mêmes arguments et le même a2.
  2. Démarrage : le crochet (stub.S) reconstitue la charge utile (MACRO et MOD), comme pour MACRO.
  3. Interface : les vérifications de test_model_tg_syntakt.py pour deux machines ajoutées, MACRO puis MOD
     (rangées, descripteurs, écran MACHINES, molette, enregistrements, changement de machine).
  4. Son :
     - sans piste MOD, par mod_vl : les 6 pistes identiques, bloc par bloc, au firmware sans MOD (32-macro-tg.json),
       machines d'origine, Sampler et MACRO mêlés, potards qui bougent, trigs ; P (les paramètres) intacts ;
     - une piste MOD ne sonne pas (sortie nulle, ni update ni étage d'ampli) ;
     - avec des pistes MOD : à l'entrée de la boucle des voix, P est EXACTEMENT celui que calcule mod.c compilé pour
       l'ordinateur (mod_ref.c) ; après l'appel, P est remis ; les 7 formes, libre et calé, trigs, profondeur des deux
       signes, les 7 potards visés et leurs bornes, plusieurs pistes MOD (une qui en module une autre, une qui se
       vise elle-même), tempo qui change, heure musicale arrêtée puis qui repart de zéro ;
     - la piste visée sonne EXACTEMENT comme si son potard avait été tourné à la valeur modulée (le même firmware,
       sans piste MOD, P donné tel quel) ;
     - calé : la période est celle de la durée choisie (1 mesure à 120 BPM : 3 000 blocs ; noire : 750) ; libre :
       celle de la table.
  5. Coût : instructions par bloc de mod_vl, mod_pre et mod_post (0, 1 et 6 pistes MOD).

    python3 tools/emu/test_mod.py --cycles model-cycles_OS1.13.syx [--quick]
"""
import argparse
import json
import pathlib
import struct
import subprocess
import sys
import tempfile

import numpy as np
from unicorn import UC_HOOK_CODE
from unicorn import m68k_const as mk

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import gen_mod                      # noqa: E402
import gen_syntakt_engines as gs    # noqa: E402
import mcengine as E                # noqa: E402
import test_macro as tm             # noqa: E402
import test_model_tg_syntakt as tms  # noqa: E402
import test_sdvintage as T          # noqa: E402
import test_sdvintage_7th as t7     # noqa: E402

check = t7.check
BASE = E.BASE
MOD = gen_mod.MOD_INDEX
MACRO = gs.TG_FIRST
SAMPLER = 6
TEMPO, SONG_TIME = 0x40149310, 0x8000184c
P_WORDS = 33
KNOBS = ("pitch", "decay", "color", "shape", "sweep", "contour", "finetune")     # Param 0..6
SLOTS = (0x0a, 0x12, 0x0b, 0x0c, 0x0d, 0x0e, 0x11)


class Fw(tm.Fw):
    """Le firmware de test_macro.py, avec n machines ajoutées (MACRO en 7, MOD en 8)."""

    def __init__(self, stock, tweaks, n=1):
        super().__init__(stock, tweaks)
        if not self.payload:
            return
        self.nm = gs.TG_FIRST + n
        self.index = MACRO
        data = self.pay + gs.LAYOUT["DATA"]
        u32 = lambda va: struct.unpack_from(">I", self.runtime, va - self.pay)[0]
        self.upd = u32(data + 4 * self.nm + 4 * self.index)
        self.rnd = u32(data + 8 * self.nm + 4 * self.index)
        self.tables = [(u32(data + 4 * self.nm + 4 * m), u32(data + 8 * self.nm + 4 * m)) for m in range(self.nm)]


class Run:
    """Un moteur (mcengine) sur ce firmware : chaque bloc écrit P (comme le lissage de l'OS), le tempo et l'heure
    musicale, puis appelle entry (la boucle des voix, ou mod_vl) avec a2 = P, comme la fonction audio."""

    def __init__(self, fw, tg, entry):
        self.e = e = fw.engine()
        self.entry = entry
        e.uc.mem_write(tg["fx_off"], struct.pack(">I", 1))        # fx_early : pas de préparation des effets
        self.time = 0
        self.seen = []                                            # P à l'entrée de la boucle des voix
        e.uc.hook_add(UC_HOOK_CODE, lambda u, a, s, d: self.seen.append(bytes(u.mem_read(E.PARAMS, 14 + 66 * 6))),
                      begin=E.VOICE_LOOP, end=E.VOICE_LOOP)

    def block(self, trk, trigs, tempo=14400, dt=None, raw=None):
        e = self.e
        for t in range(6):
            e.set(t, **trk[t])
        e._write_params()
        if raw is not None:                                       # P donné tel quel (6 x 33 mots)
            e.uc.mem_write(E.PARAMS + 14, struct.pack(">198h", *raw))
        self.time = (self.time + (2 * tempo if dt is None else dt)) & 0xffffffff
        e.uc.mem_write(TEMPO, struct.pack(">i", tempo))
        e.uc.mem_write(SONG_TIME, struct.pack(">I", self.time))
        before = bytes(e.uc.mem_read(E.PARAMS, 14 + 66 * 6))
        sp = E.STACK - 0x100
        e.uc.mem_write(sp, struct.pack(">IIIII", E.STOP, E.TRACK_BASE, E.PARAMS, trigs, 0))
        e.uc.reg_write(mk.UC_M68K_REG_A7, sp)
        e.uc.reg_write(mk.UC_M68K_REG_A2, E.PARAMS)
        for i, r in enumerate((mk.UC_M68K_REG_D2, mk.UC_M68K_REG_D3, mk.UC_M68K_REG_D4, mk.UC_M68K_REG_D5,
                               mk.UC_M68K_REG_D6, mk.UC_M68K_REG_D7, mk.UC_M68K_REG_A3, mk.UC_M68K_REG_A4,
                               mk.UC_M68K_REG_A5, mk.UC_M68K_REG_A6)):
            e.uc.reg_write(r, 0x5a5a0000 + i)
        e.uc.emu_start(self.entry, E.STOP, count=5_000_000)
        kept = all(e.uc.reg_read(r) == 0x5a5a0000 + i for i, r in enumerate(
            (mk.UC_M68K_REG_D2, mk.UC_M68K_REG_D3, mk.UC_M68K_REG_D4, mk.UC_M68K_REG_D5, mk.UC_M68K_REG_D6,
             mk.UC_M68K_REG_D7, mk.UC_M68K_REG_A3, mk.UC_M68K_REG_A4, mk.UC_M68K_REG_A5, mk.UC_M68K_REG_A6))) \
            and e.uc.reg_read(mk.UC_M68K_REG_A2) == E.PARAMS and e.uc.reg_read(mk.UC_M68K_REG_A7) == sp + 4
        after = bytes(e.uc.mem_read(E.PARAMS, 14 + 66 * 6))
        out = np.frombuffer(bytes(e.uc.mem_read(E.TRACK_BASE, 6 * 0x80)), dtype=">i4").reshape(6, 32).astype(np.int64)
        return out, before, after, kept


class Ref:
    """mod.c compilé pour l'ordinateur (mod_ref.c)."""

    def __init__(self, tmp):
        exe = tmp / "mod_ref"
        subprocess.run(["gcc", "-O2", "-DMOD_HOST", f"-DMOD_INDEX={MOD}", str(HERE / "mod_ref.c"), "-o", str(exe)],
                       check=True)
        self.p = subprocess.Popen([str(exe)], stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    def block(self, tempo, time, trigs, words):
        self.p.stdin.write(struct.pack("<iII", tempo, time, trigs) + struct.pack("<198h", *words))
        self.p.stdin.flush()
        raw = self.p.stdout.read(396 + 24 + 396)
        pre = struct.unpack("<198h", raw[:396])
        outs = struct.unpack("<6i", raw[396:420])
        post = struct.unpack("<198h", raw[420:])
        return pre, outs, post

    def close(self):
        self.p.stdin.close()
        self.p.wait()


def words(p):
    return struct.unpack(">198h", p[14:])


def base_trk():
    """6 pistes : KICK, MACRO, TONE, Sampler (muet sans échantillon), MACRO, CHORD."""
    ms = (0, MACRO, 4, SAMPLER, MACRO, 5)
    return [dict(machine=m, note=60, pitch=64, color=40 + 9 * t, shape=(5 + 7 * t) % 47 if m == MACRO else 30,
                 sweep=64, contour=20, punch=0, gate=0, finetune=64, decay=60) for t, m in enumerate(ms)]


def mod_knobs(rate, wave, depth, track, param):
    """Une piste MOD : COLOR = Rate, SHAPE = Wave, SWEEP = Depth, CONTOUR = Track, DECAY = Param."""
    return dict(machine=MOD, note=60, pitch=64, color=rate, shape=wave, sweep=depth, contour=track, punch=0, gate=0,
                finetune=64, decay=param)


# --- 1. écritures ---------------------------------------------------------------------------------------------
def writes(tw, macro_tg, tg):
    w = {x["off"] + BASE: x for x in tw["writes"]}
    call = w.get(gen_mod.VL_CALL)
    vl = int(tw["symbols"]["mod_vl"], 16)
    check(call and call["old"] == "4eb9" + f"{tg['fx_early']:08x}" and call["new"] == "4eb9" + f"{vl:08x}",
          f"{gen_mod.VL_CALL:#x} : jsr {tg['fx_early']:#x} (fx_early de Model-TG) -> jsr mod_vl ({vl:#x})")
    old = {x["off"] for x in macro_tg["writes"]}
    new = {x["off"] for x in tw["writes"]}
    check(new - old == {gen_mod.VL_CALL - BASE} and old - new == set(),
          f"mêmes adresses écrites que 32-macro-tg.json, plus {gen_mod.VL_CALL:#x} ({len(new)} écritures)")
    check(set(tw["conflicts"]) >= set(macro_tg["conflicts"]) | {"macro-tg"} and tw["requires"] == ["model-tg-st"],
          "incompatible avec tout ce qui l'est avec MACRO, et avec 32-macro-tg.json ; demande model-tg-st")


def hook_code(fw, tw, tg):
    """mod_vl : désassemblé, il appelle mod_pre, la cible d'avant, puis mod_post."""
    s = {k: int(v, 16) for k, v in tw["symbols"].items()}
    code = fw.runtime[s["mod_vl"] - fw.pay:s["mod_vl"] - fw.pay + 64]
    calls = [struct.unpack(">I", code[i + 2:i + 6])[0] for i in range(0, 60, 2) if code[i:i + 2] == b"\x4e\xb9"]
    check(calls == [s["mod_pre"], tg["fx_early"], s["mod_post"]],
          f"mod_vl appelle mod_pre, fx_early ({tg['fx_early']:#x}), puis mod_post : {[hex(c) for c in calls]}")
    upd, rnd = fw.tables[MOD]
    check((upd, rnd) == (s["mod_update"], s["mod_render"]) and fw.tables[MACRO][0] == int(tw["symbols"]["macro_update"], 16),
          f"tables update/render : MACRO en {MACRO}, MOD en {MOD}")


# --- 4. son -----------------------------------------------------------------------------------------------------
def scenario(quick):
    """(nom, blocs, fonction(bloc) -> (pistes, trigs, tempo, avance de l'heure musicale ou None))."""
    out = []
    waves = range(7)
    for wv in waves:
        for rate in ((20, 70, 92) if not quick else (70,)):
            def f(b, wv=wv, rate=rate):
                trk = base_trk()
                trk[0] = mod_knobs(rate, wv, 100 if wv % 2 else 20, 2, 2)           # -> piste 2 (MACRO) COLOR
                return trk, (1 << 1) | ((1 << 0) if b in (0, 150) else 0), 14400, None
            out.append((f"Wave {wv}, Rate {rate}", 300, f))

    def params(b):                                    # les 7 potards visés, bornes, PITCH
        trk = base_trk()
        k = (b // 60) % 7
        trk[3] = mod_knobs(92, 0, 127, 5, k)
        trk[5] = mod_knobs(40, 2, 1, 2, (k + 3) % 7)
        return trk, 1 << 4 if b % 60 == 0 else 0, 14400, None
    out.append(("les 7 potards visés, profondeurs 127 et 1", 7 * 60, params))

    def meta(b):                                      # piste 1 module la profondeur de la piste 3, qui module la 5
        trk = base_trk()
        trk[0] = mod_knobs(30, 0, 120, 3, 4)          # -> piste 3, SWEEP (Depth de la piste MOD 3)
        trk[2] = mod_knobs(84, 3, 64, 5, 2)           # -> piste 5 COLOR
        trk[5] = mod_knobs(100, 5, 90, 6, 0)          # se vise elle-même : PITCH
        return trk, (1 << 4) if b % 37 == 0 else 0, 14400, None
    out.append(("une piste MOD en module une autre, une se vise elle-même", 400, meta))

    def tempo(b):                                     # tempo qui change ; heure musicale arrêtée, puis repart de zéro
        trk = base_trk()
        trk[1] = mod_knobs(80, 1, 127, 1, 2)
        t = 14400 if b < 100 else (36000 if b < 200 else 65520)
        dt = 0 if 250 <= b < 300 else (-b * 65520 if b == 300 else None)
        return trk, 0, t, dt
    out.append(("tempo de 120 à 546 BPM, heure musicale arrêtée puis remise à zéro", 400, tempo))

    def knobs_move(b):                                # les potards de la piste MOD bougent (p-locks)
        trk = base_trk()
        trk[4] = mod_knobs((b * 3) % 128, (b // 23) % 7, (b * 7) % 128, (b // 31) % 7, (b // 17) % 7)
        return trk, 1 << 4 if b % 13 == 0 else 0, 14400, None
    out.append(("Rate, Wave, Depth, Track, Param qui bougent", 260, knobs_move))
    return out


def no_mod(fw, base_fw, tg, s, blocks):
    """Sans piste MOD : identique au firmware sans MOD, P intact."""
    a, b = Run(base_fw, tg, E.VOICE_LOOP), Run(fw, tg, s["mod_vl"])
    same, intact, kept = True, True, True
    rng = np.random.default_rng(3)
    for k in range(blocks):
        trk = base_trk()
        for t in range(6):
            trk[t]["color"] = int(rng.integers(0, 128))
            trk[t]["shape"] = int(rng.integers(0, 47)) if trk[t]["machine"] == MACRO else int(rng.integers(0, 128))
        trigs = int(rng.integers(0, 64)) if k % 9 == 0 else 0
        oa, *_ = a.block(trk, trigs)
        ob, before, after, kp = b.block(trk, trigs)
        same &= np.array_equal(oa, ob)
        intact &= before == after and b.seen[-1] == before
        kept &= kp
    peak = max(int(np.abs(oa).max()), 1)
    check(same and intact and kept and not a.e.unmapped and not b.e.unmapped,
          f"sans piste MOD, par mod_vl : {blocks} blocs, les 6 pistes (KICK, MACRO, TONE, Sampler, MACRO, CHORD) "
          f"identiques au firmware sans MOD ; P intact ; registres, a2 et pile gardés")


def with_ref(fw, base_fw, tg, s, todo, tmp):
    bad, n_blocks, mods_seen, nonzero = [], 0, 0, 0
    sounding = True
    for name, blocks, f in todo:
        ref = Ref(tmp)                                # un état neuf, comme le firmware émulé
        r = Run(fw, tg, s["mod_vl"])
        plain = Run(base_fw, tg, E.VOICE_LOOP)
        ok = True
        for k in range(blocks):
            trk, trigs, tempo, dt = f(k)
            out, before, after, kept = r.block(trk, trigs, tempo, dt)
            pre, outs, post = ref.block(tempo, r.time, trigs, words(before))
            got = words(r.seen[-1])
            ok &= got == pre and after == before and post == words(before) and kept
            mods = [t for t in range(6) if trk[t]["machine"] == MOD]
            mods_seen += len(mods)
            ok &= all(not out[t].any() for t in mods)
            nonzero += any(o != 0 for o in outs)
            # la piste visée sonne comme si son potard avait été tourné : même firmware sans MOD, P = pre
            trk2 = [dict(x) for x in trk]
            for t in mods:
                trk2[t]["machine"] = 9                   # hors limites : pas de voix (comme une piste MOD)
            o2, *_ = plain.block(trk2, trigs, tempo, dt, raw=list(pre))
            for t in range(6):
                if t not in mods and not np.array_equal(out[t], o2[t]):
                    ok = False
            sounding &= bool(np.abs(out).max() > 1 << 16) or k < 2
        n_blocks += blocks
        if not ok or r.e.unmapped:
            bad.append(name)
            print(f"        ECART {name} (hors mémoire {r.e.unmapped[:2]})")
        ref.close()
    check(not bad and nonzero > 0 and mods_seen > 0,
          f"avec des pistes MOD ({len(todo)} cas, {n_blocks} blocs, {mods_seen} blocs de piste MOD) : P à l'entrée de "
          f"la boucle des voix == mod.c compilé pour l'ordinateur ; P remis après ; pistes MOD muettes ; les autres "
          f"pistes == le même firmware sans MOD avec ces potards {bad[:4]}")


def period(fw, tg, s):
    """Calé : demi-période du carré, en blocs, à 120 BPM ; libre : celle de la table."""
    st = int(s["mod_st"], 16) if isinstance(s["mod_st"], str) else s["mod_st"]
    out_off = 8 + 0 * 28 + 24                        # g[0].out : struct gen de 7 mots après last_time, started

    def run(rate, blocks):
        r = Run(fw, tg, s["mod_vl"])
        vals = []
        for k in range(blocks):
            trk = base_trk()
            trk[0] = mod_knobs(rate, 2, 127, 2, 2)
            r.block(trk, 1 if k == 0 else 0)
            vals.append(struct.unpack(">i", r.e.uc.mem_read(st + out_off, 4))[0])
        v = np.array(vals)
        edges = np.nonzero(np.diff(np.sign(v)) != 0)[0]
        return np.diff(edges)
    bar = run(64 + 4 * 3, 7000)                      # 1 mesure : 16 doubles croches à 120 BPM = 2 s = 3 000 blocs
    q = run(64 + 4 * 8, 2000)                        # noire : 750 blocs
    free = run(40, 3000)                             # 0,02 x 2000^(40/63) Hz
    f40 = 0.02 * 2000 ** (40 / 63)
    want = 1500 / f40 / 2
    check(len(bar) >= 2 and set(bar) <= {1500} and len(q) >= 2 and set(q) <= {375}
          and len(free) and abs(free.mean() - want) <= 1,
          f"calé à 120 BPM : demi-périodes {sorted(int(x) for x in set(bar))} blocs (1 mesure : 1 500) et "
          f"{sorted(int(x) for x in set(q))} "
          f"(noire : 375) ; libre, Rate 40 : {free.mean():.1f} blocs ({f40:.3f} Hz : {want:.1f})")


# --- 5. coût ----------------------------------------------------------------------------------------------------
def cost(fw, tg, s):
    lo = min(s[k] for k in ("mod_vl", "mod_pre", "mod_post", "mod_update", "mod_render"))
    hi = lo + 0x800
    res = []
    for n in (0, 1, 6):
        r = Run(fw, tg, s["mod_vl"])
        cnt = [0]
        r.e.uc.hook_add(UC_HOOK_CODE, lambda u, a, sz, d: cnt.__setitem__(0, cnt[0] + 1), begin=lo, end=hi)
        worst = 0
        for k in range(40):
            trk = base_trk()
            for t in range(n):
                trk[t] = mod_knobs(70 + t, t, 100, (t + 1) % 7, t)
            cnt[0] = 0
            r.block(trk, 0x3f if k % 10 == 0 else 0)
            worst = max(worst, cnt[0])
        res.append(worst)
    print(f"        mod_vl + mod_pre + mod_post + mod_render, au pire : {res[0]} (sans piste MOD), {res[1]} (1), "
          f"{res[2]} (6) instructions par bloc (un bloc : 166 667 cycles)")
    check(res[2] < 4000, "coût : moins de 4 000 instructions par bloc avec 6 pistes MOD")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cycles", required=True, help="model-cycles_OS1.13.syx officiel")
    ap.add_argument("--quick", action="store_true", help="moins de cas")
    ap.add_argument("--with", dest="others", default="", help="ids d'autres tweaks appliqués aussi (6ch-usbup,…)")
    args = ap.parse_args()
    stock = T.main_os_from_syx(args.cycles)
    tg_tw, macro_tg, tw = tm.load("30-model-tg-st.json"), tm.load("32-macro-tg.json"), tm.load("32-macro-mod-tg.json")
    tg = {k: int(v, 16) for k, v in tg_tw["symbols"].items()}
    s = {k: int(v, 16) for k, v in tw["symbols"].items() if v.startswith("0x")}
    gs.set_base(gs.PAY_TG)
    gs.CATALOG["macro"] = tm.gm.MACHINE
    gs.CATALOG["mod"] = gen_mod.MOD
    others = [tm.load(f) for f in sorted(p.name for p in tm.DEV.glob("*.json"))
              if f != "device.json" and tm.load(f).get("id") in args.others.split(",")]
    order = lambda ts: sorted(ts, key=lambda t: t["order"])
    fw = Fw(stock, order(others + [tg_tw, tw]), n=2)
    base_fw = Fw(stock, order(others + [tg_tw, macro_tg]), n=1)
    ref_tg = Fw(stock, order(others + [tg_tw]))
    print(f"== {tw['id']}{' avec ' + args.others if args.others else ''} : MACRO en machine {MACRO + 1}, "
          f"MOD en machine {MOD + 1}")
    print("1. écritures")
    writes(tw, macro_tg, tg)
    hook_code(fw, tw, tg)
    print("2. démarrage")
    t7.X.bootstrap_depack_ok(args.cycles, order(others + [tg_tw, tw]), None) or t7.FAIL.append("bootstrap")
    tm.boot(fw, tg)
    print("3. interface")
    tg["knob_vec"] = tms.knob_vec_at(tw)
    tms.interface(ref_tg, fw, ["macro", "mod"], tg)
    print("4. son")
    no_mod(fw, base_fw, tg, s, 120 if args.quick else 400)
    with tempfile.TemporaryDirectory() as d:
        with_ref(fw, base_fw, tg, s, scenario(args.quick), pathlib.Path(d))
    period(fw, tg, s)
    print("5. coût")
    cost(fw, tg, s)
    fail = t7.FAIL + tms.FAIL
    print("\nTOUT OK" if not fail else f"\n{len(fail)} ÉCHEC(S)")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
