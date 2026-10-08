# Construire un firmware modifié

> ⚠️ Aucune image firmware Elektron n'est fournie ici. Tu apportes **ta propre** copie de l'OS officiel,
> téléchargée sur elektron.se. Flasher un firmware modifié se fait **à tes risques** (garantie, brick possible).
> Une **interface MIDI reliée au MIDI IN** de l'appareil (jack TRS : câble jack stéréo depuis une sortie TRS,
> ou adaptateur DIN fourni) est obligatoire pour pouvoir revenir en arrière (STARTUP MENU).

## Chaîne d'outils

Python 3 uniquement, aucune dépendance externe, aucun compilateur. Le moteur bas niveau
(`tools/mtlib/`, transport SysEx + codec aPLib + conteneur ELE3/HMAC) vient de
[`drumkilla/elektron-model-tweaks`](https://github.com/drumkilla/elektron-model-tweaks) (MIT, voir `tools/mtlib/LICENSE`).
L'orchestration (`tools/build.py`) et les tables de patchs (`tweaks/`) sont propres à ce dépôt.

## Étapes

1. Télécharge `model-cycles_OS1.13.zip` sur elektron.se, dézippe-le pour obtenir `model-cycles_OS1.13.syx`
   (SHA-256 attendu `44fe5862…9800640c`).
2. Liste les patchs disponibles :
   ```sh
   python3 tools/build.py --list
   ```
3. Construis l'image. Il y a deux variantes du 6 canaux, **incompatibles entre elles** :
   ```sh
   python3 tools/build.py -i model-cycles_OS1.13.syx -t 6ch-multiout   # référence (casse l'upgrade USB)
   python3 tools/build.py -i model-cycles_OS1.13.syx -t 6ch-usbup      # garde l'upgrade USB (celle du flasher web)
   ```
   Et la machine **SD VINTAGE** (à la place de SNARE, [note 14](notes/14-machine-sd-vintage.md)), seule ou avec un 6 canaux :
   ```sh
   python3 tools/build.py -i model-cycles_OS1.13.syx -t sdvintage-snare
   python3 tools/build.py -i model-cycles_OS1.13.syx -t 6ch-usbup,sdvintage-snare
   ```
   Et les trois tweaks de [drumkilla](https://github.com/drumkilla/elektron-model-tweaks) (fichiers `01`–`03` de `tweaks/`,
   MIT) : `latching-mute`, `trig-preview`, `browser-scroll`. Notre build donne le **même MAIN OS, octet pour octet**, que leur
   propre `tweak.py` (vérifié pour chacun et pour les trois ensemble), à une retouche près : depuis le 04/10/2026, `trig-preview`
   accepte aussi le séquenceur en pause (3 octets, [note 34](notes/34-ecoute-en-pause.md)).
   ```sh
   python3 tools/build.py -i model-cycles_OS1.13.syx -t 6ch-usbup,latching-mute,trig-preview,browser-scroll
   ```
   Et le **vrai moteur SD VINTAGE du Syntakt** ([note 17](notes/17-portage-exact-syntakt.md)), extrait au build de **ton** fichier Syntakt
   (aucun octet Elektron dans le dépôt : le tweak ne contient qu'une recette de copie et une table de relocalisation).
   L'OS Syntakt 1.42 ou 1.41 : même programme audio, donc même résultat ([note 16 §1](notes/16-moteur-syntakt.md#os-142--même-programme-audio)) :
   ```sh
   python3 tools/build.py -i model-cycles_OS1.13.syx -t 6ch-usbup,sdvintage-exact --syntakt Syntakt_OS1.42.syx
   ```
   Ou le même moteur en **7ᵉ machine « SDVtg »**, SNARE restant la SNARE d'origine ([note 18](notes/18-septieme-machine.md)) :
   ```sh
   python3 tools/build.py -i model-cycles_OS1.13.syx -t 6ch-usbup,sdvintage-7th --syntakt Syntakt_OS1.42.syx
   ```
   Ou n'importe quel choix de moteurs du Syntakt en machines ajoutées ([note 20](notes/20-moteurs-syntakt-a-cocher.md)) :
   `sdvintage-7th` (SD), `syntakt-vintage` (SD + CP), et `syntakt-<moteurs>` pour les autres choix parmi `sd`, `cp`, `toy`,
   `bits`, `swarm` (dans cet ordre : `syntakt-toy`, `syntakt-bits`, `syntakt-swarm`, `syntakt-sd-cp-toy-bits-swarm`…
   [notes 21](notes/21-sy-bits.md) et [22](notes/22-sy-swarm.md)). Un seul à la fois :
   ```sh
   python3 tools/build.py -i model-cycles_OS1.13.syx -t 6ch-usbup,syntakt-sd-cp-toy-bits-swarm --syntakt Syntakt_OS1.42.syx
   ```
   → écrit `model-cycles_OS1.13_mod.syx` à côté. `-t a,b` combine plusieurs patchs compatibles.
   `--all` applique tout, mais refuse si deux patchs sont incompatibles (c'est le cas de ces deux variantes).

Le build vérifie chaque octet `old` avant écriture, contrôle le SHA-256 de la section 3 d'origine,
et recalcule tous les checksums + le HMAC-SHA256. Seule la **section 3 (MAIN OS)** est touchée :
bootloader et updater sont conservés à l'identique.

Pour un patch qui écrit dans une zone `0xFF` (une « cave »), comme `6ch-usbup` ou `sdvintage-snare`, le build affiche la zone :
du début du bloc `0xFF` à la fin des octets écrits.
Il **refuse** si l'image d'origine contient un pointeur vers elle : `--force-cave` passe outre, après vérification à la main.
Exception : un pointeur que les patchs choisis réécrivent eux-mêmes, vers une adresse hors de la zone, est affiché « neutralisé » et ne bloque pas.
Autre exception, vérifiée à la main et listée dans `device.json` (`cave_refs_ok`) : une référence dont seule une partie de la
zone est lue. C'est le cas de `browser-scroll` : il écrit dans la table caractère → glyphe de la petite police de chiffres
(`0x401485ee`, 256 entrées de 16 bits, `0xFFFF` = pas de glyphe), aux entrées des caractères de contrôle 1 à 8, jamais dessinés.
La référence ne bloque plus tant que toutes les écritures restent dans la partie déclarée libre ; une écriture qui en sort est refusée.
C'est le cas des sprites dont on libère le masque `0xFF` en les redirigeant vers un masque identique
([note 14 §5](notes/14-machine-sd-vintage.md#5-place-libre--les-caves-0xff-étaient-des-masques-de-sprites), `tools/sprites.py`).
Les références relatives (`(d16,PC)`, branchements) sont seulement signalées, car une donnée peut les imiter.
Détails : [`notes/13-6ch-upgrade-usb.md`](notes/13-6ch-upgrade-usb.md) §4.

### Vérification de reproductibilité

Le patch 6 canaux doit produire un **MAIN OS décompressé** de SHA-256
`65e24b50dd457444e87daea79dd41b82f61098cb8ae5cd27cbe0d91742f29555`
(identique au résultat connu-bon de `ms-multi-output`). Pour l'exiger :
```sh
python3 tools/build.py -i model-cycles_OS1.13.syx -t 6ch-multiout \
    --expect-mainos 65e24b50dd457444e87daea79dd41b82f61098cb8ae5cd27cbe0d91742f29555
```
Le hash du `.syx` lui-même varie selon le packer ; c'est le MAIN OS décompressé qui fait foi.

Les autres patchs n'ont pas de résultat connu-bon extérieur. Voici ce que donne `build.py` sur l'OS 1.13 officiel
(le flasher web exige les mêmes valeurs pour celles qu'il propose) :

| `-t` | MAIN OS patché (SHA-256) |
|---|---|
| `6ch-usbup` | `57fa258f209c10359f6888af79087f656e994930ff86ffcc1e2e2e48512d9530` |
| `sdvintage-snare` (v2, recalée sur le Syntakt) | `11049726efa102028f7365b0a672c8a244b98edcef5e7c8b5e0aafba369f60d3` |
| `6ch-multiout,sdvintage-snare` | `80d0d717c5054f9277c2ddadb588e3578968cc1427610c7367ee408fa8ffd2de` |
| `6ch-usbup,sdvintage-snare` | `9416ac0b7f0bffda6b91490aacf1b00f831196466f5f64dc87b3a3b63cbc9ccb` |
| `latching-mute,trig-preview,browser-scroll` | `c6aea7e51d1caf3e9be4553f3a0c5b033592d804cb25226ef9df3fd19add8c08` (`tweak.py` de drumkilla, sans la retouche de note 34 : `71fef138…`) |
| `6ch-usbup,latching-mute,trig-preview,browser-scroll` | `b8911cb1692476c266fdad6fae7bdabd824bd8976149cc5a2baae4dfb9252176` |

| `sdvintage-exact` (avec `--syntakt Syntakt_OS1.42.syx`) | `8e2290a79fb1406ce65b3af3c5d3d95faade666e98eb8ecce0c0fa25fe87b15d` |
| `6ch-usbup,sdvintage-exact` (idem) | `bd729d526c992fb5d678a1a2793d012fdcec47f6584631fc01afa896483fc061` |
| `syntakt-vintage` (idem) | `b4de3ec5f7eda7504bf03e7141f57bae6cf5bc137d39f6704da60881d56ed9b0` |
| `sdvintage-7th` (idem) | `c73ad4c796b94ab39d106d0798091eb39b5e3e31f66d7fe78e2db30db44daad3` |
| `syntakt-cp` (idem) | `e9e3a8c7aa438a00cc028574b78dac33a6b7a44cdf98623eaa1edcf4668ac1ee` |
| `syntakt-toy` (idem) | `a3d8fad221ed0920ec15ea0e791e3be3e869b04b147affefcda4108d0248796c` |
| `syntakt-sd-cp-toy` (idem) | `ea04669bbe10d83505cd9c2b4654f46c53dd1fc55d4d5229d2cad33c6e5f0a0e` |
| `syntakt-bits` (idem) | `0e73d4f76bb947db6392b7e1e1547f2951ec8fe1924668bec532ee9b531f93bd` |
| `syntakt-sd-cp-toy-bits` (idem) | `c6add70bea5a1f6d0d4bf6aa26ae454dc6f873c6ab55937edbd7d9ad7d96c86c` |
| `syntakt-swarm` (idem) | `b47c9d7d2fb8d9568503033a3d0f318396c4b52b8df34c081564005c92b69517` |
| `syntakt-sd-cp-toy-bits-swarm` (idem) | `c6cdfe22fe5a280c1e42ae1f11ae25156d86b36da1e9d0e83f1d6c3227117b7c` |

### Model-TG

`tweaks/model-cycles_OS1.13/30-model-tg.json` vient du build de [Model-TG](https://github.com/TinyGregAudio/Model-TG) lui-même (licence MIT),
au commit épinglé dans `tools/gen_model_tg.py`, avec les binutils m68k, depuis une copie de sa source avec deux retouches (`MC_PATCHES` : en
mode mute, chaque touche de piste mute tout de suite, [note 31 §10](notes/31-model-tg.md) ; sa copie de l'écoute d'un pas accepte le
séquenceur en pause, [note 34](notes/34-ecoute-en-pause.md) ; avec l'audio USB multipiste, une piste mutée n'est plus coupée net sur sa
piste USB, [note 35 §3](notes/35-glitches-usb-multipiste.md)), plus l'envoi à l'USB à heure fixe (`tools/usb_steady.py`) :
```sh
git clone https://github.com/TinyGregAudio/Model-TG vendor/Model-TG
git -C vendor/Model-TG checkout 70b39dd6787770ebefc7a2d78dea1678ec012679   # v1.1.0
python3 tools/gen_model_tg.py --cycles model-cycles_OS1.13.syx --model-tg vendor/Model-TG [--check]
```

| `-t` | MAIN OS patché (SHA-256) |
|---|---|
| `model-tg` | `aa0740d714d502d440ec003cca8ca389c1ac9190de28395aef748ec9bf5c4c3a` (sans nos ajouts : `a049d724…`, celle annoncée par Model-TG pour sa v1.1.0) |
| `6ch-usbup,model-tg` | `96b6aec20df7c66f4a8d84c3ab4358a92b01d754c319923927d272aed0d34322` |

Le même script écrit `30-model-tg-st.json`, la base de la **version combinée avec les moteurs du Syntakt** : Model-TG construit par son
build depuis une copie de sa source, avec les mêmes ajouts et une retouche de plus (sa zone d'échantillons s'arrête 1 Mo plus bas). Les moteurs s'appliquent
par-dessus (`31-syntakt-tg-<moteurs>.json`, `requires`), voir [note 31 §4](notes/31-model-tg.md) :
```sh
python3 tools/gen_syntakt_engines.py --cycles model-cycles_OS1.13.syx --syntakt Syntakt_OS1.42.syx --all --tg [--check]
python3 tools/build.py -i model-cycles_OS1.13.syx --syntakt Syntakt_OS1.42.syx -t model-tg-st,syntakt-tg-sd-cp-toy-bits-swarm
python3 tools/emu/test_model_tg_syntakt.py --cycles model-cycles_OS1.13.syx --syntakt Syntakt_OS1.42.syx
```

| `-t` | MAIN OS patché (SHA-256) |
|---|---|
| `model-tg-st,syntakt-tg-sd` | `f09ab68d48b7cfb58604ce6dc31f0998def25390e0a02aa83ecd304754ef8321` |
| `model-tg-st,syntakt-tg-sd-cp-toy-bits-swarm` | `d5e73e10a07042a85e9022e87e488b6ca4960ecef69f49652d5c3dc706a1f6fc` |
| `6ch-usbup,model-tg-st,syntakt-tg-sd-cp-toy-bits-swarm` | `70354db7660a9ffc60956d523d2976299be4fa4813e483c1222abe26edae2b3c` |

Les 2 303 combinaisons proposées par le flasher web sont toutes listées dans `docs/flasher/app.js` (`REF_MAINOS`), calculées par
`tools/ref_mainos.py` (qui réécrit le bloc ; `--check` pour vérifier) ;
`tools/webflash_smoke.sh model-cycles_OS1.13.syx Syntakt_OS1.42.syx` les reconstruit toutes dans la page et les compare.
SD VINTAGE v1 (clean-room d'origine, **testée sur le matériel** le 29/09/2026, compilée par GCC 13.3) donnait `80b7b2bd…` seule et `38754937…` avec `6ch-usbup` ;
la v2 est compilée par `m68k-elf-gcc` 16.2 (Homebrew), voir [note 16 §6](notes/16-moteur-syntakt.md).

### Arpégiateur

`tweaks/model-cycles_OS1.13/40-arp.json` est produit par `tools/gen_arp.py`, qui compile `tools/machines/arp/` (`m68k-elf-gcc`)
et le lie dans quatre masques de sprites libérés ([note 32](notes/32-arpegiateur.md)). La preuve fait tourner le code du tweak
et celui de l'OS, jusqu'à la vraie boucle d'événements de l'interruption audio, et le live rec par les vraies fonctions
d'envoi de note de l'OS :
```sh
python3 tools/gen_arp.py --cycles model-cycles_OS1.13.syx [--check]
python3 tools/emu/test_arp.py --cycles model-cycles_OS1.13.syx \
    [--with 6ch-usbup,model-tg-st,syntakt-tg-sd-cp-toy-bits-swarm --syntakt Syntakt_OS1.42.syx]
```

| `-t` | MAIN OS patché (SHA-256) |
|---|---|
| `arp` | `e445bf895123d3fc94762a65739558b579ec8df3000c9bdf5747a3d73287e6a6` |
| `model-tg,arp` | `062f16db10bae6d9d3c6fda89aff382bf2896b531a6b511c236bb07c632247be` |
| `6ch-usbup,model-tg-st,syntakt-tg-sd-cp-toy-bits-swarm,arp` | `f933b2b99cf4d525e9529c0c6c445f2f45b94b5f479d3157a0821f566a3ac592` |

L'arpégiateur et les moteurs du Syntakt libèrent le même masque (`0x4016cae8`) : les deux constructeurs (`tools/build.py`,
`docs/flasher/builder.js`) acceptent une écriture déjà faite à l'identique par un autre tweak.

### Effacer un trig

`tweaks/model-cycles_OS1.13/41-trig-hold.json` est produit par `tools/gen_trig_hold.py`, qui assemble
`tools/machines/trig_hold/trig_hold.S` (166 o avec l'heure d'appui des 16 touches de pas) au début du masque de sprite libéré
`0x4015c044`, devant les stubs de `6ch-usbup` ([note 33](notes/33-effacer-un-trig.md)). La preuve fait tourner la vraie chaîne de
l'OS, de la lecture des touches (anti-rebond, horloge de maintien à 120 Hz) au mode grille, à la milliseconde :
```sh
python3 tools/gen_trig_hold.py --cycles model-cycles_OS1.13.syx [--check]
python3 tools/emu/test_trig_hold.py --cycles model-cycles_OS1.13.syx \
    [--with 6ch-usbup,model-tg-st,arp,syntakt-tg-sd-cp-toy-bits-swarm --syntakt Syntakt_OS1.42.syx]
```

| `-t` | MAIN OS patché (SHA-256) |
|---|---|
| `trig-hold` | `bbb8a4217a888c46a60cfb17ae444bec9cf21d6ff2d2d36bf8d1f1cdb81d1ef4` |
| `6ch-usbup,trig-hold` | `5be28240875818efd2b912680bb589300cd51a2c0aa4a86ae289b3241fbf7f44` |
| `model-tg,trig-hold` | `f71e6ea6d7bc530e3ad255b161babbfa388a93595d74c671a4ed9793bde5c87c` |
| `6ch-usbup,model-tg-st,syntakt-tg-sd-cp-toy-bits-swarm,trig-hold,arp` | `ea715e19eec998366c2c8e4ef40c354060dd3550fc42a89b46e77b71256f4ba2` |

Avec `6ch-usbup`, les deux tweaks réécrivent de la même façon le pointeur du sprite dont le masque est libéré.

### Tempo jusqu'à 546 BPM

`tweaks/model-cycles_OS1.13/42-tempo-max.json` est produit par `tools/gen_tempo_max.py` : les six bornes à 300 BPM (moteur,
horloge MIDI reçue, projet, pattern, menu Tempo) passent à 546,0 BPM, plafond du champ 16 bits du projet, ainsi que les deux
contrôles au chargement ; la remise dans le cycle de la phase du LFO devient une boucle (au-delà de 351,6 BPM, le pas peut
dépasser un cycle). 12 écritures, aucune place libre ([note 38](notes/38-tempo-546-bpm.md)). La preuve fait tourner le vrai
code de l'OS, d'origine et modifié, jusqu'aux 6 LFO (EMAC exacte) :
```sh
python3 tools/gen_tempo_max.py --cycles model-cycles_OS1.13.syx [--check]
python3 tools/emu/test_tempo_max.py --cycles model-cycles_OS1.13.syx
```

| `-t` | MAIN OS patché (SHA-256) |
|---|---|
| `tempo-max` | `5d6417b175e89b68c76743bbcad45306ba210969ad049fc1bfa6da46e432eaef` |
| `6ch-usbup,tempo-max` | `0f7a47738be9a70aa3c8e586d28636c535d787693ddf6cc756c92b5824717d42` |
| `model-tg,tempo-max` | `97cc13b34d3f4b3440d42bdb1c692e64bf0aa8b2de54b806b14320914936ef4f` |
| `6ch-usbup,model-tg-st,syntakt-tg-sd-cp-toy-bits-swarm,trig-hold,arp,tempo-max` | `c8214dc5fe88a5ff346d8554f05753bb25acec448cb0eb23140236b10a9404d4` |

### OS Cycles pour Model:Samples (et retour par USB)

Pas un tweak : `tools/crossflash.py` met l'OS Cycles officiel dans le conteneur officiel du Model:Samples, en changeant les
32 octets de la constante de sa clé de vérification (`0x401296b2`) pour qu'il accepte la signature du Samples ; le retour
est l'OS Samples officiel, inchangé, dans le transport SysEx du Cycles ([note 41](notes/41-os-cycles-sur-samples.md)).
La clé vient du `.syx` officiel du Samples, jamais du dépôt. La preuve fait tourner les vérifications de mise à jour des
deux OS sur les deux fichiers :
```sh
python3 tools/crossflash.py --cycles model-cycles_OS1.13.syx --samples model-samples_OS1.13.syx --to samples
python3 tools/crossflash.py --cycles model-cycles_OS1.13.syx --samples model-samples_OS1.13.syx --back-samples
python3 tools/emu/test_crossflash_samples.py --cycles model-cycles_OS1.13.syx --samples model-samples_OS1.13.syx
```

| Fichier | SHA-256 |
|---|---|
| `model-cycles_OS1.13_for-model-samples.syx` | `c06c23f31e50fac6ad40cd0f633acd4a7da4f63c929dff563dae887b93105dd4` |
| son MAIN OS | `b6fbc48f7d7d07cecae3859e07270fa2298f393e8afa2643144bdb4efc317aad` |
| `model-samples_OS1.13_back-from-cycles-os.syx` | `d63ce13dd1a5039d11b60d3f69d4e88e9d0f56fb2e7350c105ec641d32083680` |
### Animation de démarrage modded-cycles

`tweaks/model-cycles_OS1.13/43-boot-anim.json` est produit par `tools/gen_boot_anim.py` : le corps de la tâche d'animation de
démarrage de l'OS (`0x40053a6c`) est réécrit à sa place, assemblé depuis `tools/machines/boot_anim/` (690 o sur 1 032, aucune
place libre). Mêmes 80 images de 20 ms, même fin ; les carrés du logo puis « modded-cycles » ([note 39](notes/39-animation-demarrage.md)).
La preuve fait tourner la tâche d'origine et la nouvelle avec le vrai code de l'écran de l'OS, jusqu'au registre du DSPI1, et
compare chaque image vue sur l'écran au modèle du générateur ; le modèle de l'écran est d'abord validé par un « 7 » des grands
chiffres de l'OS, écrit par son code de texte (l'OS compte les lignes depuis le bas, note 39 §2) ; `--gif` écrit l'animation vue :
```sh
python3 tools/gen_boot_anim.py --cycles model-cycles_OS1.13.syx [--check]
python3 tools/emu/test_boot_anim.py --cycles model-cycles_OS1.13.syx [--gif anim.gif] \
    [--with 6ch-usbup,model-tg-st,syntakt-tg-sd-cp-toy-bits-swarm,arp,trig-hold,tempo-max]
```

| `-t` | MAIN OS patché (SHA-256) |
|---|---|
| `boot-anim` | `388ed6c6ee65529e2dc2d2c93aadb37c6878324acf90ad931d5ca3f7c6b68b27` |
| `6ch-usbup,boot-anim` | `76b1a532c8119ce222a3545b8a21b4cf0b2e12887e40c32f9a80b0d70975533e` |
| `model-tg,boot-anim` | `cbec181684805bf37dd07c62dc47f7daf35e6abab530a3f3b06ec5db590278ec` |
| `6ch-usbup,model-tg-st,syntakt-tg-sd-cp-toy-bits-swarm,trig-hold,arp,tempo-max,boot-anim` | `d468a729f32780870dbd591e52e8c782a5cdd347459ac1c61ef35473e26c9323` |

### Machine MACRO (les modèles de Braids)

`tweaks/model-cycles_OS1.13/25-macro.json` (7e machine) et `32-macro-tg.json` (avec Model-TG : 8e machine, par-dessus
`model-tg-st`) sont produits par `tools/gen_macro.py` : le code de Braids d'Émilie Gillet (MIT,
[pichenettes/eurorack](https://github.com/pichenettes/eurorack) au commit `08460a6`, stmlib `e3bd7c9`) compilé tel quel
avec la passerelle `tools/machines/macro/macro.cc`, en charge utile rangée après l'image et reconstituée au démarrage à
`0x43000000` (`0x46700000` avec Model-TG), avec la mécanique des machines ajoutées des moteurs du Syntakt
([note 43](notes/43-machine-macro.md)). Le générateur a besoin du clone d'eurorack (sous `vendor/`, ignoré par git) et de
`m68k-linux-gnu-g++` (les JSON versionnés viennent du GCC 13.3 d'Ubuntu 24.04 : un autre GCC donne d'autres octets) ; la
preuve, de `g++` pour Braids compilé pour l'ordinateur, la référence :
```sh
git clone https://github.com/pichenettes/eurorack vendor/eurorack
git -C vendor/eurorack checkout 08460a69a7e1f7a81c5a2abcc7189c9a6b7208d4
git -C vendor/eurorack submodule update --init stmlib
python3 tools/gen_macro.py --cycles model-cycles_OS1.13.syx --eurorack vendor/eurorack [--check]
python3 tools/emu/test_macro.py --cycles model-cycles_OS1.13.syx --eurorack vendor/eurorack [--quick] \
    [--with 6ch-usbup,model-tg-st,trig-hold,arp,tempo-max,boot-anim]
```
Incompatible avec les moteurs du Syntakt et SD VINTAGE (même mécanique, même place).

| `-t` | MAIN OS patché (SHA-256) |
|---|---|
| `macro` | `beb70b58001c12da348427b5f46f33567a6d6c2178cf7f4b7902741757e83e33` |
| `6ch-usbup,macro` | `32932ae6eca06406f1c954368dc3465cf83e1026128b39ebfc7e746e9e01023f` |
| `model-tg-st,macro-tg` | `d738fafaa86bbac86e328e6f0b70dd9688c1d9c3b05a423c957751c74abbd8d8` |
| `6ch-usbup,model-tg-st,macro-tg,trig-hold,arp,tempo-max,boot-anim` | `f584f099bd2a26abfcccf3d954dd3b86ab1880c07a0b110bce88d569eed1daeb` |

### FX : delay Tape et reverb Plate au choix, par pattern

`tweaks/model-cycles_OS1.13/44-fx-tg.json` (avec Model-TG, après `model-tg-st`) et `45-fx-macro-tg.json` (avec Model-TG
et MACRO, après `model-tg-st` et `macro-tg`) sont produits par `tools/gen_fx.py`, qui compile `tools/machines/fx/`
(`m68k-linux-gnu-gcc` ; les JSON versionnés viennent de GCC 13.3 et des binutils 2.41, un autre compilateur donne d'autres
octets) pour `0x46750000` et le range après l'image, derrière un crochet de démarrage qui l'y recopie
([note 44](notes/44-effets-au-choix.md)). Settings tenue
+ DELAY SEND ou REVERB SEND choisit l'algorithme, enregistré avec le pattern ; « Original » laisse le code de l'OS
inchangé. **Jamais essayé sur la machine** : hors du flasher web, à flasher avec une interface MIDI à portée
([FLASH.md](FLASH.md)). La preuve fait tourner le delay et la reverb de l'OS en entier (eDMA modélisé) et compare Tape et
Plate, échantillon par échantillon, à `fx.c` compilé pour l'ordinateur (`gcc`) :
```sh
python3 tools/gen_fx.py --cycles model-cycles_OS1.13.syx [--check]
python3 tools/emu/test_fx.py --cycles model-cycles_OS1.13.syx [--with 6ch-usbup] [--no-macro] [--quick] [--only 3,8]   # ~25 min
python3 tools/build.py -i model-cycles_OS1.13.syx -t 6ch-usbup,model-tg-st,macro-tg,fx-macro-tg
```
Incompatible avec les moteurs du Syntakt (même place en mémoire) et avec tout autre tweak qui ajoute une charge utile.

| `-t` | MAIN OS patché (SHA-256) |
|---|---|
| `model-tg-st,fx-tg` | `d1f401a98f4e72050149fe531702eab41f97c22bd3f55f4a2b087af6a1bd75c4` |
| `6ch-usbup,model-tg-st,fx-tg` | `5cedb25f35d56bb7983b9689a1f2cf7e06e6188fb873e4051f9210231ce6be15` |
| `model-tg-st,macro-tg,fx-macro-tg` | `8bca8a435bc60cf214c0c21dd76b479bfb479efbe7584bae4d55bad062a0ab4a` |
| `6ch-usbup,model-tg-st,macro-tg,fx-macro-tg` | `bf3fb2c29df7ce37178a6f174d6e934fc33fdec4d58dee4955373101914a1ac2` |

### Écoute d'un pas en pause

`trig-preview` (et sa copie dans Model-TG) accepte le séquenceur en pause, où le met un Stop MIDI reçu même à l'arrêt : 3 octets
retouchés en `0x40148a30` ([note 34](notes/34-ecoute-en-pause.md)). La preuve passe par le vrai gestionnaire du Stop MIDI de l'OS,
puis par le mode grille :
```sh
python3 tools/emu/test_trig_preview.py --cycles model-cycles_OS1.13.syx \
    [--with 6ch-usbup,model-tg-st,syntakt-tg-sd-cp-toy-bits-swarm,arp,trig-hold --syntakt Syntakt_OS1.42.syx]
```

### Variante `6ch-usbup`

`tweaks/model-cycles_OS1.13/11-6ch-usbup.json` n'est pas écrit à la main : `tools/relocate_6ch.py` le dérive du patch 6 canaux
(stubs déplacés dans une cave ; descripteurs et table des modes USB laissés d'origine), sans l'image firmware, avec les binutils m68k.
Depuis la [note 35](notes/35-glitches-usb-multipiste.md), il rend aussi le flux USB robuste : envoi de chaque bloc au début de
l'interruption suivante (`tools/machines/usb6/feed.S`, écritures dans `tools/usb_steady.py`, les mêmes que dans Model-TG et les moteurs
du Syntakt), file alignée au démarrage, ring de 9 cases de 168 o au lieu de 8 de 192 o, copie des 6 pistes déroulée
(`tools/machines/usb6/tracks6.S`). La preuve fait tourner le vrai pilote USB de l'OS avec un contrôleur modélisé :
```sh
python3 tools/relocate_6ch.py --check                  # le fichier versionné est-il à jour ?
python3 tools/emu/test_usb_in.py --cycles model-cycles_OS1.13.syx [--seconds 2] [--long 60]   # ~15 min
```

### Machine SD VINTAGE

`tweaks/model-cycles_OS1.13/20-sdvintage-snare.json` est produit par `tools/gen_sdvintage.py`, qui compile
`tools/machines/sdvintage/sdvintage.c` pour le ColdFire du M:C (paquet `gcc-m68k-linux-gnu`, testé avec GCC 13.3).
La validation rejoue le vrai moteur de l'OS dans un émulateur (paquets Python `unicorn` et `numpy`, plus `binutils-m68k-linux-gnu`) :
```sh
python3 tools/gen_sdvintage.py --check                               # le JSON versionné correspond-il au source ?
python3 tools/emu/test_sdvintage.py -i model-cycles_OS1.13.syx       # ~2 min ; --wav dossier/ pour écouter les rendus
```
Le vrai moteur du Syntakt, à la place de SNARE ([note 17](notes/17-portage-exact-syntakt.md)) ou en 7ᵉ machine SDVtg
([note 18](notes/18-septieme-machine.md)), a ses générateurs et ses preuves (`m68k-elf-gcc` / `m68k-elf-objdump`) :
```sh
python3 tools/gen_sdvintage_exact.py --syntakt Syntakt_OS1.42.syx --check
python3 tools/gen_sdvintage_7th.py --cycles model-cycles_OS1.13.syx --syntakt Syntakt_OS1.42.syx --check
python3 tools/emu/test_sdvintage_exact.py --cycles model-cycles_OS1.13.syx --syntakt Syntakt_OS1.42.syx
python3 tools/emu/test_sdvintage_7th.py --cycles model-cycles_OS1.13.syx --syntakt Syntakt_OS1.42.syx
python3 tools/gen_syntakt_machines.py --cycles model-cycles_OS1.13.syx --syntakt Syntakt_OS1.42.syx --check
python3 tools/emu/test_syntakt_machines.py --cycles model-cycles_OS1.13.syx --syntakt Syntakt_OS1.42.syx
python3 tools/gen_syntakt_engines.py --cycles model-cycles_OS1.13.syx --syntakt Syntakt_OS1.42.syx --all --check
python3 tools/emu/test_syntakt_machines.py --cycles model-cycles_OS1.13.syx --syntakt Syntakt_OS1.42.syx \
    --tweak tweaks/model-cycles_OS1.13/24-syntakt-sd-cp-toy.json
python3 tools/emu/test_sram_scratch.py --cycles model-cycles_OS1.13.syx --syntakt Syntakt_OS1.42.syx \
    --tweak tweaks/model-cycles_OS1.13/24-syntakt-sd-cp-toy-bits-swarm.json   # SRAM empruntée (notes/26)
python3 tools/emu/test_sram_code.py --cycles model-cycles_OS1.13.syx --syntakt Syntakt_OS1.42.syx \
    --tweak tweaks/model-cycles_OS1.13/24-syntakt-sd-cp-toy-bits-swarm.json   # code du Syntakt en SRAM (notes/28)
python3 tools/emu/test_model_tg.py --cycles model-cycles_OS1.13.syx                                  # Model-TG seul (notes/31, notes/35 §3)
python3 tools/emu/test_model_tg_syntakt.py --cycles model-cycles_OS1.13.syx --syntakt Syntakt_OS1.42.syx   # version combinée
python3 tools/ref_mainos.py --cycles model-cycles_OS1.13.syx --syntakt Syntakt_OS1.42.syx --check
```
Pour ajouter un moteur : l'ajouter à `CATALOG` de `tools/gen_syntakt_engines.py` (et ses plages copiées), puis relancer
`gen_syntakt_engines.py --all` et `--all --tg`, `gen_flasher_tweaks.py` et `ref_mainos.py`, et tester chaque nouveau tweak en émulation.

## Flasher (rappel)

1. **STARTUP MENU** : éteindre, maintenir **[FUNC]**, allumer, **[TRIG 4]** (OS UPGRADE).
2. Envoyer le `.syx` modifié sur le **MIDI IN** de l'appareil (`flash.sh` / `flash.bat`, SysEx Librarian ou C6),
   par câble jack stéréo depuis une interface à sortie TRS, ou par l'adaptateur DIN fourni.
   L'upgrade par le menu de démarrage **ne marche pas en USB MIDI** — MIDI IN obligatoire.
3. Détails, tests et récupération : [`notes/06-flash-et-recuperation.md`](notes/06-flash-et-recuperation.md).

## Autre chaîne d'outils

Le C [`mischa85/elektron-firmware-tool`](https://github.com/mischa85/elektron-firmware-tool) fait le même travail
de conteneur ; voir [`notes/02-format-os-syx.md`](notes/02-format-os-syx.md).
