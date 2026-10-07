#!/usr/bin/env python3
"""Génère les tweaks FX : algorithmes de delay et de reverb au choix, par pattern (notes/44).

  - 44-fx-tg.json : avec Model-TG (s'ajoute après model-tg-st) ;
  - 45-fx-macro-tg.json : avec Model-TG et la machine MACRO (s'ajoute après model-tg-st et macro-tg).

tools/machines/fx/ : fx.c (Tape, Plate, le choix par pattern, l'interface) et fx_hooks.S (les accroches), compilés
et liés ici juste après l'image (la fin du tweak précédent : le code s'exécute en place, sans crochet de démarrage).
Les deux mémoires (bande, plaque) sont en BSS à 0x46750000, dans le dernier Mo de la zone de Model-TG, après la charge
utile de MACRO. Trois écritures sur l'OS (HOOKS) : l'entrée du delay et celle de la reverb de l'OS, et le point de
passage des encodeurs, que Model-TG a déjà détourné (l'écriture part de SES octets : le tweak vient après lui).

La reverb Plate reprend la topologie et les réglages de clouds/dsp/fx/reverb.h (Émilie Gillet, MIT), réécrits en
virgule fixe dans fx.c : aucun octet de son code compilé. Aucun octet Elektron.

    python3 tools/gen_fx.py --cycles model-cycles_OS1.13.syx [--check]
"""
import argparse
import json
import pathlib
import struct
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "emu"))

import build                       # noqa: E402
import gen_sdvintage_exact as gx   # noqa: E402
import test_sdvintage as T         # noqa: E402

SRC = HERE / "machines" / "fx"
DEV = HERE.parent / "tweaks" / "model-cycles_OS1.13"
BASE = gx.BASE
BSS = 0x46750000                   # fin de la charge utile de MACRO avec Model-TG : 0x4674f2f0 (notes/43 §4)
BSS_END = 0x46800000               # fin du Mo laissé par Model-TG (notes/31 §4)
# -O2 : le coût par bloc compte (notes/44 §7) ; pas de bibliothèque : ni division 64 bits ni flottant dans fx.c
CFLAGS = ["-mcpu=54418", "-O2", "-ffreestanding", "-fno-builtin", "-nostdlib", "-fno-pic", "-fno-common",
          "-ffunction-sections", "-fdata-sections", "-fomit-frame-pointer", "-Wall", "-Wextra", "-Werror"]
LINK = """SECTIONS
{{
  .text {code:#x} : {{
    *(.text.hooks) *(.text*) . = ALIGN(4); *(.rodata*) . = ALIGN(4); *(.data*)
  }}
  .bss {bss:#x} (NOLOAD) : {{ *(.bss*) *(COMMON) }}
  /DISCARD/ : {{ *(.comment) *(.note*) *(.eh_frame*) }}
}}
"""
# (id, ordre, fichier, tweaks sur lesquels il s'ajoute)
VARIANTS = (
    ("fx-tg", 44, "44-fx-tg.json", ("model-tg-st",)),
    ("fx-macro-tg", 45, "45-fx-macro-tg.json", ("model-tg-st", "macro-tg")),
)
# (adresse, symbole, instruction, octets attendus : None = ceux de l'OS d'origine, sinon le symbole de Model-TG appelé)
HOOKS = (
    (0x40057488, "fx_delay_entry", 0x4ef9, "4fefffd048d77cfc", None,
     "entrée du delay : lea -48(sp),sp ; movem.l d2-d7/a2-a6,(sp) -> jmp fx_delay_entry ; nop"),
    (0x400579c4, "fx_reverb_entry", 0x4ef9, "4fefffcc48d77cfc", None,
     "entrée de la reverb : lea -52(sp),sp ; movem.l d2-d7/a2-a6,(sp) -> jmp fx_reverb_entry ; nop"),
    (0x400081ce, "fx_enc_hook", 0x4eb9, None, "gm_enc_gate",
     "passage des encodeurs : jsr gm_enc_gate (Model-TG, à la place de jsr 0x40006158) -> jsr fx_enc_hook"),
)
TG_SYMBOLS = ("set_held", "mod_used", "show_popup", "gm_enc_gate")
SYMBOLS = ("fx_latch", "fx_tape", "fx_plate", "fx_ui_turn", "fx_delay_entry", "fx_reverb_entry", "fx_enc_hook", "st",
           "ring", "pbuf")


def compile_fx(at, tg):
    """Code lié à l'adresse at : (octets, symboles, fin du BSS)."""
    defs = [f"-DTG_{n.upper()}={int(tg[n], 16):#x}" for n in TG_SYMBOLS]
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        obj, hooks, elf, ld, out = d / "fx.o", d / "hooks.o", d / "fx.elf", d / "fx.ld", d / "fx.bin"
        gx.run([gx.CROSS + "gcc", *CFLAGS, *defs, "-c", str(SRC / "fx.c"), "-o", str(obj)])
        gx.run([gx.CROSS + "gcc", "-mcpu=54418", *defs, "-c", str(SRC / "fx_hooks.S"), "-o", str(hooks)])
        ld.write_text(LINK.format(code=at, bss=BSS), encoding="utf-8")
        gx.run([gx.CROSS + "ld", "-T", str(ld), "--no-warn-rwx-segments", "-o", str(elf), str(hooks), str(obj)])
        undef = gx.run([gx.CROSS + "nm", "-u", str(elf)]).strip()
        if undef:
            raise SystemExit(f"!! symboles non résolus (bibliothèque du compilateur ?) : {undef}")
        syms = {}
        for line in gx.run([gx.CROSS + "nm", str(elf)]).splitlines():
            p = line.split()
            if len(p) == 3:
                syms[p[2]] = int(p[0], 16)
        gx.run([gx.CROSS + "objcopy", "-O", "binary", "-j", ".text", str(elf), str(out)])
        code = out.read_bytes()
        bss_end = max(int(l.split()[0], 16) + int(l.split()[1], 16)
                      for l in gx.run([gx.CROSS + "nm", "-S", str(elf)]).splitlines()
                      if len(l.split()) == 4 and l.split()[2] in "bB")
    return code, syms, bss_end


def build_tweak(stock, tweaks, tid, order, requires):
    base = [tweaks[i] for i in requires]
    tg = tweaks["model-tg-st"]["symbols"]
    patched, _ = build.apply_writes(stock, base)             # l'OS tel que le trouvent nos écritures
    payload, _ = build.build_payload(base, stock, None)
    at = BASE + len(stock) + len(payload)
    last = base[-1]["append"]
    if int(last["dest"], 16) != int(last["at"], 16) and int(last["dest"], 16) + last["size"] > BSS:
        raise SystemExit(f"!! la charge utile de {base[-1]['id']} dépasse {BSS:#x}")
    code, syms, bss_end = compile_fx(at, tg)
    if at + len(code) > build.END_LIMIT:
        raise SystemExit(f"!! l'OS agrandi dépasserait {build.END_LIMIT:#x}")
    if bss_end > BSS_END:
        raise SystemExit(f"!! BSS jusqu'à {bss_end:#x} : au-delà de {BSS_END:#x}")
    writes = []
    for va, sym, op, stock_hex, tg_sym, _ in HOOKS:
        if tg_sym:                                           # l'appel que Model-TG a mis là
            old = struct.pack(">HI", 0x4eb9, int(tg[tg_sym], 16))
        else:
            old = bytes.fromhex(stock_hex)
            if stock[va - BASE:va - BASE + len(old)] != old:
                raise SystemExit(f"!! octets d'origine inattendus en {va:#x}")
        if patched[va - BASE:va - BASE + len(old)] != old:
            raise SystemExit(f"!! {va:#x} : un tweak de {requires} y a écrit autre chose que prévu")
        new = struct.pack(">HI", op, syms[sym])
        new += b"\x4e\x71" * ((len(old) - len(new)) // 2)
        writes.append({"off": va - BASE, "old": old.hex(), "new": new.hex()})
    writes.sort(key=lambda w: w["off"])
    # tout autre tweak qui ajoute une charge utile déplacerait la fin de l'image
    others = sorted(t["id"] for t in tweaks.values()
                    if t["id"] not in requires and t["id"] != tid and (t.get("append") or t["id"] == "model-tg"))
    tweak = {
        "id": tid,
        "order": order,
        "name": "FX : delay Tape et reverb Plate au choix, par pattern (avec Model-TG"
                + (" et MACRO)" if "macro-tg" in requires else ")"),
        "description": [
            "Settings tenue + potard DELAY SEND : l'algorithme du delay (Original, Tape) ; + REVERB SEND : celui de la",
            "reverb (Original, Plate). Le choix est enregistré avec le pattern (octet +512 des pistes 1 et 2, bits 5-7,",
            "inutilisé par l'OS) ; un pattern sans choix garde les effets d'origine, inchangés.",
            "Tape : écho à bande (pleurage, saturation, répétitions de plus en plus sombres ; 2,7 s au plus, Fdbk à",
            "fond : l'écho s'emballe). Plate : la reverb des modules d'Émilie Gillet (clouds/dsp/fx/reverb.h, MIT),",
            "réécrite en virgule fixe et calculée à 24 kHz. TIME, FDBK, SIZE et TONE gardent leur rôle.",
            f"S'ajoute après {' et '.join(requires)} : {len(code)} o de code en place à {at:#x}, mémoires en BSS",
            f"({BSS:#x}..{bss_end:#x}). Expérimental : jamais essayé sur la machine.",
            "Généré par tools/gen_fx.py, notes/44.",
        ],
        "device": "Model:Cycles",
        "os": "1.13",
        "section": 3,
        "requires": list(requires),
        "conflicts": others,
        "symbols": {n: f"{syms[n]:#x}" for n in SYMBOLS},
        "writes": writes,
        "append": {"at": f"{at:#x}", "dest": f"{at:#x}", "size": len(code),
                   "parts": [{"dest": f"{at:#x}", "hex": code.hex()}], "reloc": []},
    }
    build.apply_writes(patched, [tweak])                     # les octets attendus collent
    return tweak


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cycles", required=True, help="model-cycles_OS1.13.syx officiel")
    ap.add_argument("--check", action="store_true", help="vérifie que les JSON versionnés correspondent")
    args = ap.parse_args()
    stock = T.main_os_from_syx(args.cycles)
    dev, tweaks = build.load_catalog()[DEV.name]
    if build.sha(stock) != dev["section_sha256"]:
        raise SystemExit("!! ce n'est pas le MAIN OS 1.13 officiel")
    bad = 0
    for tid, order, name, requires in VARIANTS:
        tweak = build_tweak(stock, tweaks, tid, order, requires)
        text = json.dumps(tweak, indent=1, ensure_ascii=False) + "\n"
        out = DEV / name
        print("  " + tweak["description"][-3])
        if args.check:
            ok = out.exists() and out.read_text(encoding="utf-8") == text
            print(f"  {name} {'est à jour' if ok else 'NE CORRESPOND PAS (autre GCC ?)'}")
            bad += not ok
            continue
        out.write_text(text, encoding="utf-8")
        print(f"  écrit : {out.relative_to(HERE.parent)} ({len(tweak['writes'])} écritures, "
              f"{tweak['append']['size']} o ajoutés)")
    raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    main()
