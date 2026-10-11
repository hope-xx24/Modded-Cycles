# 57 — Machine MOD : un LFO et un aléatoire qui modulent les potards des autres pistes

Demande du propriétaire du fork `hope-xx24` (11/10/2026) : « un travail modulaire sur la machine, autour de Braids, avec
des générateurs simples ». Choix faits avec lui : une **machine MOD posée sur une piste** d'abord (des générateurs
dans les pistes MACRO viendront ensuite) ; pour commencer, un **LFO calé sur le tempo** et un **aléatoire tenu ou
lissé** ; cibles : **les potards de machine de n'importe quelle piste**. Tweak `32-macro-mod-tg.json` (Model-TG + MACRO
+ MOD, à la place de `32-macro-tg.json`), générateur `tools/gen_mod.py`, code `tools/machines/mod/` (`mod.c`,
`mod_hooks.S`), preuve `tools/emu/test_mod.py` (référence `tools/emu/mod_ref.c`). Avec FX ([56](56-effets-au-choix.md)) :
`58-fx-macro-mod-tg.json`. Adresses : VA de l'OS 1.13.

## Réponse courte

- **MOD est la 9e machine** (après le Sampler de Model-TG et MACRO). Une piste MOD ne sonne pas : ses potards règlent
  un générateur, qui s'ajoute au potard d'une piste (la sienne ou une autre) à chaque bloc audio (1 500 fois par
  seconde) :

  | Potard | Nom | Rôle |
  |---|---|---|
  | COLOR | Rate (RATE) | 0..63 libre, de 0,02 Hz à 40 Hz (exponentiel) ; 64..127 calé sur le tempo, 16 durées de 4 crans : 8, 4, 2 mesures, 1 mesure, blanche pointée, blanche, noire pointée, blanche de triolet, noire, croche pointée, noire de triolet, croche, double croche pointée, croche de triolet, double croche, double croche de triolet |
  | SHAPE | Wave (WAVE) | 0 sinus, 1 triangle, 2 carré, 3 dent de scie montante, 4 descendante, 5 aléatoire tenu (S&H), 6 aléatoire lissé |
  | SWEEP | Depth (DPTH) | 64 : rien ; au-dessus la modulation, en dessous la même inversée ; ±128 à fond (±32 demi-tons pour PITCH) |
  | CONTOUR | Track (TRK) | piste visée, 1..6 ; 0 : aucune |
  | DECAY | Param (PARM) | potard visé : 0 PITCH, 1 DECAY, 2 COLOR, 3 SHAPE, 4 SWEEP, 5 CONTOUR, 6 FINE |

- Un **trig** de la piste MOD remet la phase à zéro et tire une nouvelle valeur aléatoire. Les potards de la piste MOD
  se verrouillent par pas comme ceux de toute machine, et **son LFO d'origine les module** (un LFO sur la vitesse d'un
  autre). Une piste MOD peut viser une **autre piste MOD** (sa profondeur, sa vitesse) : un petit patch modulaire.
- La modulation agit **exactement comme si l'on tournait le potard** visé (`[FAIT en émulation]`, §5) : avec MACRO,
  COLOR = Timbre, SHAPE = le modèle de Braids, SWEEP = Color, FINE = le lo-fi RATE/BITS ([55](55-macro-allegee-et-lofi.md)).
- **Coût** : 120 instructions par bloc sans piste MOD, 820 avec une, 2 080 avec six (un bloc : 166 667 cycles, donc
  moins de 1,3 %) ; une piste MOD ne fait rien tourner d'autre (ni machine, ni étage d'ampli).
- **Jamais essayé sur la machine.** À vérifier : §7.

## 1. Ce que fait l'OS `[FAIT]`

| Où | Quoi |
|---|---|
| `0x40059872` | fonction audio : `a2` = `0x40058474(…)` puis `0x40056e24` (lissage des paramètres) et `0x40091ab2(a2, tempo, …)` (les 6 LFO, [38](38-tempo-546-bpm.md)) ; puis l'appel de la boucle des voix |
| `0x4005981e` | `jsr 0x400a7d4a` (boucle des voix) avec `(0x80001858, P = a2, trigs, relâchés)` ; Model-TG le remplace par `jsr fx_early` (`0x401b715c`), qui lance la préparation des effets puis saute à la boucle, **en lisant `a2`** |
| `P + 14 + 66 t` | les paramètres lissés de la piste t, mots 8.8, rangés par l'emplacement des descripteurs ([14 §1](14-machine-sd-vintage.md)) : 9 machine (octet fort), 10 PITCH, 11 COLOR, 12 SHAPE, 13 SWEEP, 14 CONTOUR, 15 PUNCH, 16 GATE, 17 FINE, 18 DECAY, 19 Volume, 20/21 envois, 22 pan. C'est le `p` que reçoit `update` de chaque machine |
| `0x8000184c` | heure musicale : + 2 × tempo par bloc ; une double croche = 5 400 000, 1/6 de double croche (une impulsion d'horloge MIDI) = 900 000 |
| `0x40149310` | tempo, en 1/120 de BPM |

`P` sert aussi de mémoire au lissage de l'OS (`0x40056e24` part de la valeur du bloc précédent) : y ajouter une
modulation sans la retirer l'y accumulerait. D'où le principe : ajouter avant la boucle des voix, remettre après.

## 2. Conception

- `mod_vl` (`mod_hooks.S`) prend l'appel de `0x4005981e` : `mod_pre(P, trigs)`, puis la cible d'avant (`fx_early` de
  Model-TG) avec les mêmes 4 arguments et le même `a2`, puis `mod_post(P)`. `d0-d1/a0-a1` seuls changent, comme pour
  l'appel d'origine.
- `mod_pre` : si une piste au moins est sur MOD (octet fort du mot 9), garde les 7 potards visables des 6 pistes
  (42 mots), puis calcule chaque piste MOD **dans l'ordre des pistes** et ajoute sa sortie au potard visé, borné
  (PITCH : `0x2800..0x5800`, comme son descripteur ; les autres 0..127). Une piste MOD lit ses propres potards après
  les modulations des pistes MOD précédentes : la piste 1 peut moduler la profondeur de la piste 3.
  `mod_post` remet les 42 mots.
- Générateur (`struct gen`, 28 o par piste) : libre, phase Q32 + table `FREE_INC` (0,02 × 2000^(i/63) Hz) ; calé, une
  position dans le cycle, en unités de l'heure musicale (cycle = durée × 900 000), avancée de ce que l'heure a avancé ;
  la phase en est tirée par une division de 16 bits (`frac16`). Heure arrêtée (l'OS à l'arrêt, `[HYP]`) : on avance
  au tempo, le LFO continue ; heure qui saute (départ, retour en arrière) : chaque cycle se recale dessus. Formes en
  Q15 ; aléatoire : un générateur congruentiel par piste, une valeur par cycle (et par trig).
- La machine : `mod_update` ne fait rien, `mod_render` écrit 32 zéros. Le détour de MACRO avec Model-TG
  (`gen_macro.dispatch_tg_asm`, avec `quiet`) envoie la machine 8 droit sur une sortie nulle, sans `update`, sans
  `render`, sans l'étage d'ampli de Model-TG.
- Machines ajoutées : la mécanique de MACRO pour deux machines (`gen_macro.build_tweak(extra=…)`, celle des moteurs
  du Syntakt, [20](20-moteurs-syntakt-a-cocher.md)) : tables des noms, `update`/`render`, descripteurs 76..80 (MACRO)
  et 81..85 (MOD), bornes. Le 5e descripteur de MOD (DECAY) est renommé « Param », borné à 0..6.

## 3. Place

- **MACRO n'est pas recompilée** : son code et ses tables sont repris octet pour octet de `32-macro-tg.json` (la
  version testée sur la machine le 11/10/2026), MOD est liée juste après (`0x46718450..`), ses variables (264 o)
  après celles de MACRO. La charge utile passe de 324 576 à 324 840 o (jusqu'à `0x4674f4e8`, sous `0x46750000` où FX
  recopie son code), rangée en 107 480 o.
- `gen_macro.build_tweak` reconstruit `32-macro-tg.json` **à l'identique** avec ces octets repris (vérifié) : la
  généralisation à plusieurs machines ne change rien à MACRO seule.
- Une écriture de plus que `32-macro-tg.json` : `0x4005981e` (`jsr 0x401b715c` → `jsr mod_vl`).

## 4. Conflits

Ceux de MACRO avec Model-TG (moteurs du Syntakt, autres machines ajoutées) et `macro-tg` lui-même. FX : la variante
`58-fx-macro-mod-tg.json` s'ajoute après `macro-mod-tg` (même code, fin d'image différente).

## 5. Preuve en émulation `[FAIT en émulation]`

`python3 tools/emu/test_mod.py --cycles model-cycles_OS1.13.syx [--quick] [--with 6ch-usbup]` (seul : 25 cas ; avec `6ch-usbup`, `--quick` : 11 cas ; TOUT OK les deux fois) :

| Vérification | Résultat |
|---|---|
| écritures : celles de `32-macro-tg.json` + `0x4005981e` ; `mod_vl` appelle `mod_pre`, `fx_early`, `mod_post` | ok |
| démarrage : bootstrap, crochet, charge utile reconstituée, SRAM intacte | ok |
| interface (`test_model_tg_syntakt.interface`, 2 machines) : rangées, descripteurs, CC, enregistrements, changement de machine 7 → 8, écran MACHINES (9 repères), noms « MACRO », « MOD », potards | ok |
| sans piste MOD, par `mod_vl` : les 6 pistes (KICK, MACRO, TONE, Sampler, MACRO, CHORD) identiques bloc par bloc à `32-macro-tg.json`, P intact, registres, `a2` et pile gardés | ok |
| avec des pistes MOD : P à l'entrée de la boucle des voix == `mod.c` compilé pour l'ordinateur, P remis après, pistes MOD muettes, les autres pistes == le même firmware sans MOD avec ces potards ; 7 formes × 3 vitesses, les 7 cibles et leurs bornes, une piste MOD qui en module une autre, une qui se vise, tempo de 120 à 546 BPM, heure musicale arrêtée puis remise à zéro, potards de la piste MOD qui bougent | ok |
| calé à 120 BPM : demi-période 1 500 blocs (1 mesure) et 375 (noire) ; libre, Rate 40 : 300,8 blocs (2,494 Hz) | ok |
| coût au pire : 120 / 820 / 2 080 instructions par bloc (0, 1, 6 pistes MOD) | ok |

FX par-dessus (`test_fx.py --mod`) : voir [56](56-effets-au-choix.md).

## 6. Limites

- Une piste MOD par cible ; plusieurs pistes MOD peuvent viser le même potard (leurs modulations s'ajoutent).
- Les valeurs s'affichent en chiffres (Wave 0..6, Track 0..6, Param 0..6) : l'OS n'a pas de libellés par valeur.
- Couper (mute) une piste MOD ne coupe pas sa modulation (elle lit ses potards, pas sa sortie).
- PUNCH, GATE, Volume, pan et envois ne sont pas visables (ils ne sont pas des potards de machine ; Volume, pan et
  envois sont lus après la boucle des voix).

## 7. À vérifier sur la machine `[À FAIRE]`

Firmware : `build.py -t 6ch-usbup,model-tg-st,macro-mod-tg[,fx-macro-mod-tg]`.

1. Les 9 machines dans l'écran MACHINES ; MOD s'appelle « MOD », ses potards Rate, Wave, Depth, Track, Param
   (Param : affiché 0..6 ou 0..127 ?).
2. Piste 1 en MOD, Track 2, Param 2 (COLOR), Depth 100, Wave 0, Rate 76 : le COLOR de la piste 2 balance en une
   mesure. À l'arrêt du séquenceur : le LFO continue-t-il ? Au départ : repart-il du début de la mesure ?
3. Wave 5 et 6 : pas aléatoires ; un trig de la piste MOD en tire un nouveau.
4. Avec MACRO en piste 2 : Param 3 (SHAPE) fait défiler les modèles, Param 6 (FINE) le lo-fi.
5. Sauvegarder, recharger le projet : la piste MOD et ses réglages restent.
6. Page System de Model-TG : charge avec 1 et 6 pistes MOD.

## 8. Suite

- Des générateurs dans les pistes MACRO elles-mêmes (sans prendre de piste), comme choisi au départ.
- D'autres formes : enveloppe déclenchée par les trigs (Peaks, MIT), suiveur d'enveloppe d'une autre piste.
