# 44 — Delay et reverb au choix : Tape et Plate, par pattern

Demande du propriétaire de ce fork (`hope-xx24/Modded-Cycles`), le 08/10/2026 : d'autres algorithmes d'effet,
choisis avec **Settings + le potard de l'effet**, sur le modèle de la machine MACRO (du code libre porté sur le
Cycles). Liste souhaitée : reverbs Dark, Plate, Shimmer, Supervoid ; delays Tape, Digital, Echo, Analog, Granular.
Premier jalon retenu avec lui : **un delay Tape et une reverb Plate** en plus des effets d'origine, choisis avec
Settings + DELAY SEND / REVERB SEND, **enregistrés par pattern**, à côté de MACRO, Model-TG et de l'audio USB
6 canaux, pour sa machine. Tweaks `44-fx-tg.json` (avec Model-TG) et `45-fx-macro-tg.json` (avec Model-TG et MACRO),
générateur `tools/gen_fx.py`, sources `tools/machines/fx/`, preuve `tools/emu/test_fx.py` (référence
`tools/emu/fx_ref.c`). Adresses : VA de l'OS 1.13.

## Réponse courte

- **Changer d'algorithme est faisable** : l'étage de sortie appelle le delay puis la reverb par deux fonctions aux
  arguments simples (sortie, entrée, deux paramètres), une fois par bloc (§1). Un détour à l'entrée de chacune suffit,
  avec un crochet de démarrage qui met le code à l'abri (§8) ;
  avec « Original », le code de l'OS continue, identique à l'échantillon près (§6). La [note 10](10-faisabilite-fonctionnalites.md)
  classait la chose hors de portée : c'était avant les charges utiles ajoutées à l'image ([17](17-portage-exact-syntakt.md),
  [43](43-machine-macro.md)) et avant de savoir où sont les deux entrées.
- **Tape** : un écho à bande écrit pour ce projet (tête de lecture interpolée, pleurage, saturation, répétitions de plus
  en plus sombres et minces, temps qui glisse). Il reprend la loi de saturation et le gain du delay d'origine, mesurés :
  même niveau, même course de FDBK (§3).
- **Plate** : la reverb des modules d'Émilie Gillet (`clouds/dsp/fx/reverb.h`, MIT), réécrite en virgule fixe, le
  ColdFire n'ayant pas de FPU, et calculée à 24 kHz ; SIZE et le niveau sont réglés sur la reverb d'origine, mesurée (§4, §5).
- **Coût** : Tape + Plate coûtent **9 % d'instructions de moins** que le delay et la reverb d'origine (9 881 contre
  10 864 par bloc), avec plus de mémoire lente touchée ; à peu près neutre au total (§7). À mesurer sur la machine
  (page System de Model-TG).
- **Jamais essayé sur la machine.** Tout est prouvé en émulation (§6) ; le niveau, la couleur et la charge réelle
  restent à écouter et à mesurer (§9). Hors du flasher web : `build.py` seulement.
- Le reste de la liste : §10.

## 1. Ce que fait l'OS `[FAIT]`

| Où | Quoi |
|---|---|
| `0x4005979e` | fonction audio, une fois par bloc de 32 trames : lissage des paramètres (`0x40058474`, rend la structure d'état), boucle des voix, mixeur, puis `0x400567ba(…, état)` |
| `0x400567ba` | étage de sortie : coefficients des pistes (pan, envois), bus d'envoi du delay (stéréo, `0x8000b990`), **delay**, bus de la reverb (pistes + sortie du delay × son envoi), **reverb**, puis le mix : 6 pistes + retour du delay + retour de la reverb |
| `0x40057488(out, in, état + 428)` | **delay**, appelé en `0x4005699c`. `out` = `0x8000ba90`, `in` = `0x8000b990` : 32 trames stéréo entrelacées, Q31. Paramètres : Time (+428) et Fdbk (+430), mots 8.8 |
| `0x400579c4(out, in, état + 432)` | **reverb**, appelée en `0x40056a2c`. `out` = `0x8000bb90`. Paramètres : Size (+432, son octet haut) et Tone (+434) |
| `0x4005802e(état)` | préparation des deux effets, en tête de l'étage : adresses des lignes de la reverb, temps du delay, et un transfert DMA (eDMA, canal 30) qui ramène en SRAM ce que les deux effets vont lire ; l'étage attend sa fin (`0x4005697e`) |
| `0x40057942`, `0x400573fa` | après la reverb : pas des lignes, et le DMA de retour (canal 42) qui réécrit en SDRAM ce que les effets ont produit |
| `0x400582c4` | initialisation au démarrage (`0x40057260` delay, `0x4005770e` reverb) |

Le delay et la reverb ne touchent donc que la SRAM : leur mémoire (delay : 524 288 trames stéréo à `0x4a400000`, 10,9 s ;
reverb : 9 lignes de 64 Ko à `0x4a340000` et 8 de 4 Ko à `0x4a3d0000`) va et vient par DMA, par tranches de 128 o (256 pour
le delay), avec les modulos de l'eDMA pour le bouclage.

- **Temps du delay** (`0x4005819a`) : `((Time + 256) × 48000 >> 10) × 900 / tempo` trames, tempo en 1/120 de BPM
  ([38](38-tempo-546-bpm.md)), plafonné à 384 000 (8 s) : une unité de TIME = 1/128 de ronde (750 trames à 120 BPM). Lissé
  (constante de 256 blocs), deux têtes en fondu.
- **Le delay d'origine** : dans sa boucle, une saturation `2x − x|x|` (gain 2 aux petits niveaux), un passe-bas à
  **1,44 kHz** (pôle `0x4013ff84` = 0,8286) et un passe-haut à 15,5 Hz (pôle `0x4013fb84` = 0,9980). Réinjection =
  Fdbk / 127.
- Avec Model-TG ([31](31-model-tg.md)) : les trois appels passent par ses enveloppes `fx_a`, `fx_b`, `fx_c`, qui sautent
  les effets après 16 384 blocs de silence (entrées et sorties sous −90 dB) ; `fx_a` finit par `jmp 0x40057488`, `fx_b`
  appelle `0x400579c4`. Un détour **à l'entrée des deux fonctions de l'OS** est donc pris dans les deux cas, et l'arrêt
  au silence de Model-TG vaut pour nos algorithmes sans rien y changer.

### Interface

| Où | Quoi |
|---|---|
| `0x400081ce` | `jsr 0x40006158(racine, événement)` : le point de passage de tous les événements d'encodeur (Model-TG y a mis `jsr gm_enc_gate`). Événement : +12 = encodeur, +16 = crans (signés), +20 = rapide |
| encodeurs | 1 = LEVEL/DATA ; 2..15 = les potards, dans l'ordre de l'enregistrement de la page (`0x400e0fc6`) : PITCH, DECAY, COLOR, SHAPE, SWEEP, CONTOUR, **8 = DELAY SEND**, **9 = REVERB SEND**, LFO SPEED, VOLUME+DIST, SWING, CHANCE, 14 = REVERB SIZE (Tone avec FUNC), 15 = DELAY TIME (Fdbk) |
| Settings | touche 13. Model-TG en fait le modificateur de ses accords : `set_held` tant qu'elle est tenue, `mod_used` dès qu'un accord a servi (son relâchement n'ouvre alors pas le menu Config) ; `show_popup(texte)` affiche un message |

### Où ranger le choix

- L'octet +512 de chaque piste du pattern est libre et recopié tel quel à l'enregistrement et au chargement
  ([32 §4](32-arpegiateur.md)) ; l'arpégiateur en prend les bits 0-4. **Bits 5-7 de la piste 0 : le delay ; de la
  piste 1 : la reverb** (0 = Original). Un pattern jamais touché, ou venu d'un autre firmware, garde les effets d'origine.
- Le séquenceur joue le pattern `*0x40a7887c` (`CUR_PAT` de Model-TG), dans la banque des 96 patterns
  (`0x406fa040 + n × 30710`). `[FAIT]` Les objets « piste » de l'interface pointent dans cette même banque : l'objet du
  projet est lié à `0x406fa024` (`0x40006cae`), et `0x4000ea46` lie le pattern n à `données + 0x1c + n × 30710`. La
  [note 32 §10](32-arpegiateur.md) concluait à « une autre copie » ; par prudence l'interface écrit **aux deux endroits**
  (le pattern joué, et les données de l'objet de la piste si elles sont ailleurs), et le côté audio relit le pattern joué à
  chaque bloc : un changement de pattern, de projet, une copie ou un effacement de pattern sont suivis sans rien de plus.
  `[HYP]` La tenue après extinction repose sur la recopie de l'octet +512 (note 32) : **à vérifier sur la machine** (§9).

## 2. Conception

```
0x40057488  jmp fx_delay_entry   →  fx_latch() : le choix du pattern joué (remise à zéro de l'algorithme qui entre)
                                    Original : lea -48(sp),sp ; movem.l … ; jmp 0x40057490      (le delay de l'OS)
                                    Tape     : MACSR = 0xa0, acc0 vide ; jmp fx_tape(out, in, params)
0x400579c4  jmp fx_reverb_entry  →  Original : lea -52(sp),sp ; movem.l … ; jmp 0x400579cc
                                    Plate    : jmp fx_plate(out, in, params)
0x400081ce  jsr fx_enc_hook      →  Settings tenue et encodeur 8 ou 9 : fx_ui_turn ; sinon jmp gm_enc_gate
0x40000530  jsr fx_boot          →  au démarrage : le code recopié à 0x46750000, puis le crochet précédent (§8)
```

- Les algorithmes prennent exactement la place des fonctions de l'OS : mêmes arguments, registres gardés
  (d2-d7/a2-a6), accumulateurs de l'EMAC rendus vides comme elles. `d0-d1/a0-a1` sont libres à l'entrée (les deux
  fonctions les chargent avant de les lire).
- **La mémoire des effets d'origine pendant ce temps** : leurs transferts DMA continuent (la préparation et le retour
  sont communs aux deux effets). Sans rien faire, la mémoire de l'effet remplacé tournerait en rond, et il rejouerait
  son ancienne queue au retour. Tape met donc à zéro, à chaque bloc, le tampon que le DMA réécrit dans la mémoire du delay
  (elle est vide après un tour, 10,9 s) ; Plate fait de même pour les 17 tampons de la reverb pendant 560 blocs (un tour de
  ses lignes les plus longues), et les deux remettent les filtres de l'effet d'origine comme l'initialisation de l'OS les
  laisse.
- **Remise à zéro** : un algorithme repart vide chaque fois qu'il est choisi. Tape ne relit que ce qu'il a écrit depuis
  (compteur `valid`), sans effacer ses 512 Ko ; Plate efface ses 64 Ko en 8 blocs (5 ms de silence).
- **Interface** : Settings tenue, le premier cran du potard affiche le choix en cours (« Delay FX / Tape »), puis
  4 crans passent au suivant ou au précédent, avec un popup à chaque changement. Le potard ne règle pas l'envoi pendant ce
  temps. Relâcher Settings n'ouvre pas le menu Config (comme après un accord de Model-TG).

## 3. Tape

Écrit pour ce projet (`fx_tape`). La bande tourne à **24 kHz** : son passe-bas de lecture est à 2,5 kHz, et le calcul
est divisé par deux. Entrée ramenée à 24 kHz par (1 2 1) / 4, sortie par interpolation linéaire.

| | Tape | Delay d'origine (§1) |
|---|---|---|
| Mémoire | 131 072 trames à 24 kHz, 16 bits : **5,4 s** au plus | 10,9 s, 32 bits (temps : 8 s au plus) |
| Temps | la même formule (TIME, tempo) ; il **glisse** quand on le change (12 trames par bloc au plus : la hauteur plonge ou monte, comme un moteur) | deux têtes en fondu |
| Lecture | interpolée, avec pleurage (0,6 Hz, ±0,4 ms) et scintillement (5,7 Hz) | fixe |
| Dans la boucle | passe-bas **2,5 kHz**, passe-haut **130 Hz** : chaque répétition plus sombre et plus mince | passe-bas 1,44 kHz, passe-haut 15,5 Hz |
| Saturation | à l'enregistrement, `2v − v|v|`, celle du delay d'origine | la même |
| Réinjection | Fdbk / 127 × 0,9 | Fdbk / 127 |

Mesures (salve de 1 kHz à −40 dBFS, TIME 1, émulation pour l'origine, référence pour Tape) :

| | 1er écho / entrée | écho suivant / écho, FDBK 64 | FDBK 127 |
|---|---|---|---|
| Delay d'origine | 1,637 | 0,826 | × 1,63 par répétition (il s'emballe, la saturation le tient) |
| Tape | 1,782 | 0,818 | × 1,62 |

Au-delà de FDBK ≈ 78, les deux s'emballent : c'est le comportement de l'OS, gardé.

## 4. Plate

La topologie et les réglages de `clouds/dsp/fx/reverb.h` (pichenettes/eurorack, commit `08460a6`, Émilie Gillet, licence
MIT) : la reverb de Clouds, Rings et Elements, « Griesinger » décrite par Dattorro : 4 passe-tout en entrée, puis une
boucle de 2 × (passe-bas, 2 passe-tout, 1 retard), le premier passe-tout brouillé et un retard modulé par deux
oscillateurs lents. Son code est en flottant : `fx_plate` le **réécrit en virgule fixe** (aucun octet de son code
compilé ; ce n'est pas un portage « tel quel » comme Braids).

| | Clouds | Plate |
|---|---|---|
| Fréquence | 32 kHz | **24 kHz** : entrée (G + D) par un filtre demi-bande (−1 0 9 16 9 0 −1) / 32, sortie par le même |
| Lignes | 113, 162, 241, 399 ; 1653, 2038, 3411 ; 1913, 1663, 4782 | × 0,75 : 85, 121, 181, 299 ; 1241, 1529, 2559 ; 1435, 1247, 3587 |
| Mémoire | 16 384 mots de 12 bits | 16 384 mots de 32 bits (Q27, à 1/8 de l'échelle de Clouds : jamais de débordement) |
| Diffusion | 0,7 | 0,7 en entrée ; 0,35..0,7 dans la boucle, avec SIZE |
| Gain de la boucle | 0,35..0,98 | 0..0,97 avec SIZE (0,0075 par cran jusqu'à 100, puis 0,0081) |
| Passe-bas de la boucle | 0,6..0,97 | 0,06..0,6 (TONE 64)..0,97 |
| Modulation | 0,5 et 0,3 Hz | les mêmes, un pas par bloc |

## 5. Niveau et décroissance : réglés sur la reverb d'origine `[FAIT en émulation]`

Bruit décorrélé G/D à −20 dBFS pendant 1 s, puis silence ; reverb d'origine en émulation (son vrai code, DMA modélisé),
Plate par la référence. Niveau en régime établi, puis niveau 1 s après l'arrêt :

| SIZE (TONE 64) | D'origine | Plate |
|---|---|---|
| 0 | −33,1 dB ; −84 dB après 0,4 s | −32,7 dB ; −75 dB après 0,4 s |
| 42 (défaut) | −31,6 dB ; −82 dB | −32,4 dB ; −71 dB |
| 64 | −30,6 dB ; −62 dB | −32,0 dB ; −58 dB |
| 100 | −28,9 dB ; −44 dB | −30,8 dB ; −45 dB |
| 127 | −26,9 dB ; −30 dB (≈ 3 dB/s) | −29,1 dB ; −33 dB (≈ 4 dB/s) |

TONE n'a pas le même sens : sur la reverb d'origine c'est un passe-bas (sous 64) ou un passe-haut (au-dessus) sur toute
la reverb (−47,9 dB à TONE 20 dans la même mesure) ; sur Plate c'est l'amortissement de la boucle, du plus sombre au plus
brillant (−36,8 dB à 20, −30,6 dB à 110).

## 6. Preuve en émulation `[FAIT en émulation]`

`tools/emu/test_fx.py` : le vrai code de l'OS dans Unicorn, EMAC exacte ([14 §4](14-machine-sd-vintage.md)). Le delay
et la reverb d'origine y tournent **en entier**, avec leur préparation et leurs DMA : l'eDMA est modélisé dans le test
(TCD, modulos, chaînes scatter/gather), ce que la [note 27 §3.4](27-cinq-et-six-voix.md) n'avait pas. Référence de Tape
et de Plate : `fx.c` compilé pour l'ordinateur avec l'arithmétique de `emac.py` (`fx_ref.c`).

| Vérification | Résultat (`fx-macro-tg` sur `6ch-usbup`, `model-tg-st`, `macro-tg`) |
|---|---|
| Accroches : les 4 écritures, les 8 octets remplacés rejoués puis `jmp` à la suite, 23 octets changés et rien d'autre, aucune écriture commune avec un autre tweak (hors les deux appels repris du tweak précédent), aucune autre référence aux octets remplacés | ok |
| **Démarrage par le vrai code**, RAM remplie de `0xa5` : `jsr` de `0x40000530` → `fx_boot` → crochet de MACRO → `boot_extra_hook` de Model-TG (son `movec` vers ACR1 sauté : Unicorn ne le connaît pas) jusqu'à la reprise en `0x4000053a`. Le code est à `0x46750000` et rien d'autre n'y est écrit ; registres et pile comme sans FX ; tout ce qui suit le bloc de Model-TG est à zéro ; le reste de la mémoire (image, BSS, charge utile de MACRO) est identique au firmware sans FX | ok |
| **Original** = le même firmware sans FX, bloc par bloc : sorties du delay et de la reverb, et toute la mémoire des effets (SRAM, lignes, 4 Mo du delay) ; avec les octets du pattern à 0, les bits 0-4 pris, des valeurs hors limites, pas de pattern | ok, 4 × 288 blocs |
| **Tape** = la référence, échantillon par échantillon (TIME, FDBK et tempo qui bougent, bruit, note tenue, pleine échelle, silences) | ok, 1 440 blocs |
| **Plate** = la référence (SIZE et TONE qui bougent) | ok, 1 440 blocs |
| Registres d2-d7/a2-a6 et pile gardés, accumulateurs vides au retour ; n'écrivent que leur sortie, leur état, leur mémoire et l'état de l'effet d'origine qu'ils remplacent | ok |
| Mémoire des effets d'origine : le DMA de retour n'y écrit que du silence pendant Tape ; les 17 lignes de la reverb vides après 600 blocs de Plate ; retour à Original sans reste (sorties nulles) ; Tape et Plate repartent vides | ok |
| Par `fx_a` / `fx_b` / `fx_c` de Model-TG : Tape identique à la référence ; au silence les sorties passent sous −126 dB ; Model-TG éteint les effets après 16 384 blocs (sorties nulles, 1 436 instructions par bloc) et les rallume au premier son | ok |
| Interface : sans Settings ou avec un autre encodeur, l'événement va à `gm_enc_gate`, registres et pile gardés ; Settings + 8 / 9 : 1er cran = le choix en cours, 4 crans = le suivant, bits 0-4 de l'octet gardés, popup, notification de la piste, `mod_used` ; données de l'interface dans le pattern joué ou à part ; le choix est pris par le côté audio au bloc suivant | ok |
| **De bout en bout dans l'étage de sortie de l'OS** (`0x400567ba`, 6 pistes, envois, pan) : Original identique au firmware sans FX (mix, sorties) ; Tape et Plate appelés une fois par bloc avec les tampons de l'OS, sorties = la référence sur l'entrée reçue, retours dans le mix | ok, 192 blocs |

Le test attrape aussi ce que la compilation cache : les fonctions de l'OS rendent leurs pointeurs dans `d0`, alors que
notre GCC (`m68k-linux-gnu`) les lit dans `a0` ; `fx.c` les déclare donc entières (`ui_track`).

Durée : ~25 min (~9 min avec `--quick`).

## 7. Coût

Instructions par bloc de 32 trames, au pire sur 24 blocs de bruit (`test_fx.py` §7) :

| | Delay | Reverb | Total | Lignes de 16 o lues ou écrites en SDRAM |
|---|---|---|---|---|
| D'origine | 2 659 | 8 205 | 10 864 | 17 |
| Original, avec FX | 2 692 (+33 : le choix du pattern) | 8 209 | 10 901 | 21 |
| Tape + Plate | 3 266 | 6 615 | **9 881** | 84 |

- Tape seul : +607 instructions par bloc ; Plate seule : −1 590.
- Avec le modèle des notes [27](27-cinq-et-six-voix.md)–[28](28-code-syntakt-en-sram.md) (1,54 cycle par instruction, 15
  cycles par ligne de SDRAM), sur un bloc de 166 667 cycles : Tape + Plate ≈ **−0,3 %**, Tape avec la reverb d'origine
  ≈ +0,7 %, Plate avec le delay d'origine ≈ −1 %. `[HYP]` Ce modèle n'a pas été calé sur ce code : à mesurer.
- Les effets d'origine travaillent en SRAM (un cycle par accès) ; les nôtres ont leur mémoire en SDRAM, par le cache
  (8 Ko), comme les moteurs du Syntakt ([23](23-optimisation-charge.md)). C'est le poste incertain.
- Leur préparation et leurs DMA (≈ le reste des 12 % de la note 31) tournent toujours : ne pas les lancer quand les deux
  effets sont remplacés est un gain possible, pas pris ici (§10).

## 8. Place et démarrage

| | `fx-tg` | `fx-macro-tg` |
|---|---|---|
| S'ajoute après | `model-tg-st` | `model-tg-st`, `macro-tg` |
| Dans l'image, après le tweak précédent | crochet de démarrage (28 o) en `0x401bf8c0`, puis 4 560 o de code | en `0x401d8edc` |
| Code et état (140 o, à zéro au démarrage) à l'exécution | `0x46750000..0x467511d0` | idem |
| Mémoires (BSS) | `0x467511d0..0x467e11d0` : plaque 64 Ko, bande 512 Ko | idem |
| Crochet de démarrage | `jsr` de `0x40000530` : `fx_boot`, puis `boot_extra_hook` de Model-TG | `fx_boot`, puis le crochet de MACRO (`0x4016cae8`), puis `boot_extra_hook` |

- **Le code ne peut pas rester après l'image** `[FAIT en émulation]` : `boot_extra_hook` de Model-TG remet à zéro tout
  ce qui suit son bloc (le BSS de l'OS, `0x4019b590..0x423380b0`), et l'OS reprend ensuite ces adresses pour les 16 blocs
  du cache du système de fichiers (`0x401ab750..0x401eb750`, [31 §1](31-model-tg.md) : Model-TG n'en retire que les 6
  premiers, pour son bloc). La première version de ce tweak s'exécutait en place : la preuve des effets passait, la
  machine aurait planté au premier appel du delay. `fx_boot` (`tools/machines/fx/fx_boot.S`) recopie donc le code à
  `0x46750000` avant tout cela, comme le crochet de MACRO pour sa charge utile ; les caches ne sont pas encore en service
  (`0x40000542`).
- `0x46700000..0x46800000` est le Mo laissé par Model-TG ([31 §4](31-model-tg.md)) ; la charge utile de MACRO y finit à
  `0x4674f2f0`, et son crochet ne remet à zéro que sa zone. Les moteurs du Syntakt y mettent autre chose : incompatibles
  (`conflicts`), comme tout tweak qui ajoute une charge utile après l'image.
- Le générateur lit dans `30-model-tg-st.json` quatre adresses de Model-TG de plus (`set_held`, `mod_used`,
  `show_popup`, `gm_enc_gate`), et le test neuf autres (`fx_a`…) : `ST_SYMBOLS` de `tools/gen_model_tg.py`, les octets
  de ce tweak ne changent pas.

## 9. À vérifier sur la machine `[À FAIRE]`

Firmware : `build.py -t 6ch-usbup,model-tg-st,macro-tg,fx-macro-tg`. Une interface MIDI sur le MIDI IN d'abord
([FLASH.md](../FLASH.md)).

1. Sans rien choisir : tout sonne comme avant (delay, reverb, Model-TG, MACRO).
2. Settings tenue + DELAY SEND : popup « Delay FX / Original », puis « Tape » en tournant ; idem REVERB SEND et « Plate ».
   Relâcher Settings n'ouvre pas le menu Config. Sans Settings, les deux potards règlent les envois comme avant.
3. Tape : niveau proche du delay d'origine ; TIME et FDBK ; tourner TIME fait glisser la hauteur ; FDBK à fond s'emballe
   sans casser le son du reste.
4. Plate : niveau proche de la reverb d'origine à SIZE égal ; SIZE de 0 à 127 ; TONE.
5. Aller-retour Original ↔ Tape / Plate pendant que ça joue : pas de reste de l'ancien effet, pas de clic fort.
6. Deux patterns aux choix différents : le changement de pattern change les effets. Copier, effacer un pattern.
7. **Éteindre par le bouton, rallumer** : les choix sont-ils gardés ? (seule hypothèse du §1)
8. Charge : page System de Model-TG, même motif avec Original puis Tape + Plate. Craquements, écran ralenti ?
9. Silence prolongé (11 s) puis une note : les effets repartent.

## 10. Suite

- **Les autres algorithmes demandés.** La structure accepte 7 choix par effet (3 bits). Delays Digital, Echo et Analog :
  des réglages de la même boucle (filtres, saturation, modulation), presque sans code. Reverb Dark : Plate avec un
  amortissement fort et un passe-bas en entrée. **Shimmer** (transposition d'une octave dans la boucle) et **Granular**
  coûtent un transpositeur ou des grains : à chiffrer en émulation d'abord, à 24 kHz. « Supervoid » est un nom
  d'Elektron (Analog Four, Rytm) : aucun code à porter, ce serait une reverb à nous dans cet esprit.
- Ne plus lancer la préparation ni les DMA des effets d'origine quand les deux sont remplacés.
- Le flasher web : après l'essai sur la machine, et avec `ref_mainos.py` (il faut le fichier du Syntakt pour recalculer
  toutes les combinaisons).
