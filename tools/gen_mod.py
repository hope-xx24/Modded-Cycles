#!/usr/bin/env python3
"""Génère le tweak « macro-mod-tg » : Model-TG + la machine MACRO (Braids, notes/43) + la machine MOD (notes/57), une
machine qui ne sonne pas et module les potards de machine d'une piste : LFO libre ou calé sur le tempo, aléatoire
tenu ou lissé. Tout est dans la charge utile de MACRO (même compilation, gen_macro.build_tweak avec une machine en
plus) ; MOD est la 9e machine, après le Sampler de Model-TG et MACRO. Remplace 32-macro-tg.json (s'ajoute de même
après model-tg-st).

Accroche en plus de celles de MACRO : l'appel de la boucle des voix par la fonction audio (0x4005981e, que Model-TG
envoie déjà à son détour) passe par mod_vl (tools/machines/mod/mod_hooks.S), qui applique les modulations aux
paramètres lissés des pistes avant la boucle et les remet après.

    python3 tools/gen_mod.py --cycles model-cycles_OS1.13.syx [--check]

MACRO n'est pas recompilée : son code et ses tables sont repris octet pour octet de 32-macro-tg.json (la version
testée sur la machine), MOD est liée juste après. Seul mod.c est compilé (m68k-linux-gnu-gcc 13.3, sans g++) ; un
autre GCC donne d'autres octets.
"""
import argparse
import json
import pathlib
import struct
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "emu"))

import gen_macro as gm             # noqa: E402
import gen_sdvintage_exact as gx   # noqa: E402
import gen_sdvintage_7th as g7     # noqa: E402
import gen_syntakt_engines as gs   # noqa: E402

DEV = gm.DEV
OUT = DEV / "32-macro-mod-tg.json"
SRC = HERE / "machines" / "mod"
BASE = gm.BASE
VL_CALL = 0x4005981e               # jsr vers la boucle des voix (0x400a7d4a), ou le détour de Model-TG
MOD_INDEX = gm.TG_FIRST + 1        # 0..5 d'origine, 6 Sampler, 7 MACRO, 8 MOD
WAVES = ("SINE", "TRIANGLE", "SQUARE", "SAW UP", "SAW DOWN", "S&H", "SMOOTH")
PARAMS = ("PITCH", "DECAY", "COLOR", "SHAPE", "SWEEP", "CONTOUR", "FINE")
# image : celle de METAL (l'OS n'en a que 6) ; potards : (nom long, court, défaut[, min, max]) ; Rate 76 = 1 mesure
MOD = dict(name="MOD", image=2, decay=2,
           knobs=(("Rate", "RATE", 76), ("Wave", "WAVE", 0, 0, len(WAVES) - 1), ("Depth", "DPTH", 64),
                  ("Track", "TRK", 0, 0, 6)),
           decay_knob=("Param", "PARM", 2, 0, len(PARAMS) - 1),
           update="mod_update", render="mod_render", quiet=True,
           keep=("mod_update", "mod_render", "mod_vl", "mod_pre", "mod_post", "mod_st"))


def machine(tg):
    """La machine MOD pour gen_macro.build_tweak, avec la cible actuelle de l'appel de la boucle des voix."""
    cur = tg["img"]
    if cur[VL_CALL - BASE:VL_CALL - BASE + 2] != b"\x4e\xb9":
        raise SystemExit(f"!! {VL_CALL:#x} : jsr attendu")
    prev = struct.unpack_from(">I", cur, VL_CALL + 2 - BASE)[0]
    if prev != tg["fx_early"]:
        raise SystemExit(f"!! {VL_CALL:#x} : appel vers {prev:#x}, attendu le détour de Model-TG ({tg['fx_early']:#x})")
    m = dict(MOD)

    def link(tmp, code_at, bss_at):
        """mod.c et mod_hooks.S (gcc, sans bibliothèque), liés à code_at, variables à bss_at."""
        objs = []
        for src, opts in ((SRC / "mod.c", [*gm.CFLAGS_RT, f"-DMOD_INDEX={MOD_INDEX}"]),
                          (SRC / "mod_hooks.S", ["-mcpu=54418", f"-DMOD_NEXT={prev:#x}"])):
            o = tmp / (src.stem + "_mod.o")
            gx.run([gx.CROSS + "gcc", *opts, "-c", str(src), "-o", str(o)])
            objs.append(o)
        ld, elf, out = tmp / "mod.ld", tmp / "mod.elf", tmp / "mod.bin"
        ld.write_text(LINK.format(code=code_at, bss=bss_at))
        gx.run([gx.CROSS + "ld", "-T", str(ld), "--gc-sections", "--no-warn-rwx-segments", "-e", "mod_vl",
                *[x for k in m["keep"] for x in ("-u", k)], "-o", str(elf), *map(str, objs)])
        secs = {}
        for line in gx.run([gx.CROSS + "objdump", "-h", str(elf)]).splitlines():
            f = line.split()
            if len(f) > 4 and f[0].isdigit():
                secs[f[1]] = (int(f[3], 16), int(f[2], 16))
        if set(secs) - {".text", ".rodata", ".data", ".bss"}:
            raise SystemExit(f"!! sections inattendues : {sorted(secs)}")
        gx.run([gx.CROSS + "objcopy", "-O", "binary", "-R", ".bss", str(elf), str(out)])
        syms = {p_[2]: int(p_[0], 16) for p_ in (l.split() for l in gx.run([gx.CROSS + "nm", str(elf)]).splitlines())
                if len(p_) == 3}
        if any(not 0 <= syms[k] - code_at < 0x10000 and not 0 <= syms[k] - bss_at < 0x1000 for k in m["keep"]):
            raise SystemExit("!! symboles de MOD")
        bss = secs.get(".bss", (bss_at, 0))
        if bss[0] != bss_at:
            raise SystemExit("!! BSS de MOD")
        return out.read_bytes(), syms, (bss[0] + bss[1] + 3) & ~3
    m["link"] = link

    def writes(cur_, syms, w):
        w(VL_CALL, bytes.fromhex("4eb9") + g7.be32(prev), bytes.fromhex("4eb9") + g7.be32(syms["mod_vl"]))
    m["writes"] = writes
    m["prev"] = prev
    return m


LINK = """SECTIONS
{{
  .text {code:#x} : {{ *(.text*) }}
  .rodata : {{ *(.rodata*) }}
  .data : {{ *(.data*) }}
  .bss {bss:#x} (NOLOAD) : {{ *(.bss*) *(COMMON) }}
  /DISCARD/ : {{ *(.comment) *(.note*) *(.eh_frame*) }}
}}
"""


def macro_prebuilt():
    """MACRO telle que compilée dans 32-macro-tg.json (testée sur la machine) : son code et ses tables, ses
    symboles, la fin de ses variables. Ni g++ ni les sources de Braids : les mêmes octets."""
    tw = json.loads((DEV / "32-macro-tg.json").read_text(encoding="utf-8"))
    ap_ = tw["append"]
    part = ap_["parts"][0]
    if int(part["dest"], 16) != gs.PAY_TG or int(ap_["dest"], 16) != gs.PAY_TG:
        raise SystemExit("!! 32-macro-tg.json : charge utile attendue à PAY_TG")
    syms = {k: int(v, 16) for k, v in tw["symbols"].items()}
    syms[gm.BRAIDS_RENDER] = syms["braids_render"]
    return bytes.fromhex(part["hex"]), syms, gs.PAY_TG + ap_["size"], tw


def incompatible_with_macro():
    """Ce qui se déclare incompatible avec 32-macro-tg.json l'est avec nous (même place : MACRO avec les moteurs du
    Syntakt, etc.), hors ce qui s'ajoute par-dessus MACRO (FX : sa propre variante)."""
    out = set()
    for f in DEV.glob("*.json"):
        if f.name in ("device.json", OUT.name):
            continue
        t = json.loads(f.read_text(encoding="utf-8"))
        if "macro-tg" in t.get("conflicts", []) and not {"macro-tg", "macro-mod-tg"} & set(t.get("requires", [])):
            out.add(t["id"])
    return out - {"macro-mod-tg"}


def build_tweak(img, repo=None):
    tg = gs.tg_context(img)
    m = machine(tg)
    blob, syms0, bss_end, macro_tg = macro_prebuilt()
    out, info = gm.build_tweak(img, repo, tg, extra=[m], prebuilt=(blob, syms0, bss_end))
    syms = info["syms"]
    old = out
    out = {
        "id": "macro-mod-tg",
        "order": old["order"],
        "name": "Model-TG + MACRO + MOD : Braids et une machine qui module les potards des autres pistes",
        "description": [
            "Comme 32-macro-tg.json (Model-TG, MACRO en 8e machine, notes/43), plus la machine MOD en 9e (notes/57) :",
            "elle ne sonne pas, elle module un potard de machine d'une piste. COLOR = Rate (0..63 libre, 0,02 à 40 Hz ;",
            "64..127 calé sur le tempo, de 8 mesures à la double croche de triolet), SHAPE = Wave (" + ", ".join(WAVES) + "),",
            "SWEEP = Depth (64 : rien), CONTOUR = Track (1..6, 0 : aucune), DECAY = Param (" + ", ".join(PARAMS) + ").",
            "Un trig de la piste MOD remet sa phase à zéro et tire une nouvelle valeur aléatoire.",
            "Code de Braids (licence MIT : LICENSE-Braids) et tools/machines/mod/, compilés par tools/gen_mod.py.",
            "Aucun octet Elektron dans ce fichier.",
        ],
        "device": old["device"],
        "os": old["os"],
        "section": old["section"],
        "requires": old["requires"],
        "conflicts": sorted(set(old["conflicts"]) | {"macro-tg"} | incompatible_with_macro()),
        "writes": old["writes"],
        "append": old["append"],
        "symbols": dict(old["symbols"], **{k: f"{syms[k]:#x}" for k in m["keep"]}, mod_prev=f"{m['prev']:#x}",
                        mod_index=str(MOD_INDEX)),
    }
    return out, info


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cycles", required=True, help="model-cycles_OS1.13.syx officiel")
    ap.add_argument("--check", action="store_true", help="vérifie que le JSON versionné correspond")
    args = ap.parse_args()
    img = g7.cycles_main(args.cycles)
    tweak, info = build_tweak(img)
    print(f"  {tweak['id']} : {len(tweak['writes'])} écritures, code et tables {info['code']} o, variables "
          f"{info['bss']} o, ajout à l'image {info['image']} o ; mod_vl en {tweak['symbols']['mod_vl']}")
    raise SystemExit(1 if gm.emit(OUT, json.dumps(tweak, indent=1) + "\n", args.check) else 0)


if __name__ == "__main__":
    main()
