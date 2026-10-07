/*
 * FX : algorithmes de delay et de reverb au choix sur le Model:Cycles (MAIN OS 1.13), notes/44.
 *
 * L'étage de sortie de l'OS (0x400567ba) appelle le delay puis la reverb, une fois par bloc de 32 trames à 48 kHz :
 *     delay (out, in, params)   0x40057488   out 0x8000ba90, in 0x8000b990, params = état + 428 (Time, Fdbk : mots 8.8)
 *     reverb(out, in, params)   0x400579c4   out 0x8000bb90, in 0x8000b990, params = état + 432 (Size, Tone : mots 8.8)
 * in et out : 32 trames stéréo entrelacées (G, D), int32 Q31. Chaque effet rend 100 % d'effet ; l'OS le dose ensuite
 * (retours de delay et de reverb dans le mix, envoi du delay vers la reverb).
 *
 * fx_hooks.S détourne l'entrée de ces deux fonctions : avec l'algorithme 0 (« Original »), le code de l'OS continue,
 * inchangé ; sinon l'un des algorithmes ci-dessous prend sa place, avec les mêmes arguments :
 *   - delay 1, « Tape » : écho à bande. Tête de lecture interpolée, pleurage et scintillement, saturation à
 *     l'enregistrement, passe-bas et passe-haut dans la boucle (chaque répétition plus sombre et plus mince), temps
 *     qui glisse quand on le change. Mémoire en 16 bits, 2,7 s au plus.
 *   - reverb 1, « Plate » : la reverb des modules d'Émilie Gillet (clouds/dsp/fx/reverb.h de pichenettes/eurorack,
 *     licence MIT : topologie de Griesinger, 4 passe-tout en entrée puis une boucle de 2 x (2 passe-tout + 1 retard),
 *     modulée), réécrite en virgule fixe (le ColdFire n'a pas de FPU) et calculée à 24 kHz, retards mis à l'échelle
 *     (x 0,75 depuis ses 32 kHz).
 *
 * Choix : un octet par effet dans le pattern (octet +512 de la piste 0 pour le delay, de la piste 1 pour la reverb,
 * bits 5-7 ; inutilisé par l'OS, recopié tel quel à l'enregistrement, notes/32 §4 ; l'arpégiateur en prend les bits
 * 0-4). Le côté audio le lit à chaque bloc dans le pattern que joue le séquenceur (*0x40a7887c). L'interface le
 * change avec la touche Settings tenue (le modificateur des accords de Model-TG) et le potard DELAY SEND ou REVERB
 * SEND : fx_ui_turn, depuis le point de passage des encodeurs (0x400081ce).
 *
 * État : la petite structure st est dans l'image (à zéro au démarrage, comme les variables de Model-TG) ; les deux
 * mémoires (bande : 512 Ko ; plaque : 64 Ko) sont en BSS dans le dernier Mo de la zone de Model-TG (0x46750000..,
 * après la charge utile de MACRO), jamais remis à zéro au démarrage : la bande ne relit que ce qu'elle a écrit
 * (valid), la plaque s'efface en 8 blocs quand on la choisit. Toute multiplication fractionnaire passe par l'EMAC
 * (MACSR = 0xa0, comme l'OS) ; les accumulateurs sont rendus vides.
 *
 * FX_HOST : le même code compilé pour l'ordinateur, avec l'arithmétique exacte de l'EMAC (tools/emu/emac.py), pour la
 * preuve (tools/emu/test_fx.py compare la sortie du firmware à celle-ci, échantillon par échantillon).
 */
typedef signed char s8;
typedef unsigned char u8;
typedef short s16;
typedef unsigned short u16;
typedef int s32;
typedef unsigned int u32;

#ifdef FX_HOST
typedef long long s64;
static s64 acc_;
static inline s32 qmul(s32 a, s32 b)                 /* mac.l ; movclr.l en mode fractionnaire saturé */
{
	s64 p = (s64)a * b, v;

	acc_ += (p >> 23) << 23;
	v = acc_ >> 31;
	acc_ = 0;
	return v > 0x7fffffffLL ? 0x7fffffff : v < -0x80000000LL ? (s32)0x80000000 : (s32)v;
}
extern u8 *host_cur_pat;
extern s32 host_tempo;
extern u32 *host_dly_stage;
extern u32 host_rev_stage[];
#define CUR_PAT    host_cur_pat
#define TEMPO      host_tempo
#define DLY_STAGE  host_dly_stage
#define REV_STAGE_A host_rev_stage
#define REV_STAGE_B (host_rev_stage + 8 * 40)
#define PAT_OK(p)  ((p) != 0)
#else
static inline s32 qmul(s32 a, s32 b)
{
	s32 r;

	__asm__("mac.l %1,%2,%%acc0\n\tmovclr.l %%acc0,%0" : "=r"(r) : "r"(a), "r"(b));
	return r;
}
#define CUR_PAT    (*(u8 *volatile *)0x40a7887c)     /* le pattern que joue le séquenceur (Model-TG : CUR_PAT) */
#define TEMPO      (*(volatile s32 *)0x40149310)     /* tempo en 1/120 de BPM (notes/38) */
#define DLY_STAGE  (*(u32 *volatile *)0x8000b570)    /* tampon du delay d'origine, relu et réécrit par DMA */
#define REV_STAGE_A ((u32 *)0x8000afe0)              /* tampons de la reverb d'origine : 8 x 160 o */
#define REV_STAGE_B ((u32 *)0x8000a9b0)              /* et 9 x 176 o */
#define PAT_OK(p)  ((u32)(p) - 0x40000000u < 0x08000000u)
#endif

#define TRK_SIZE   722                               /* une piste dans le pattern */
#define CFG_OFF    512                               /* son octet libre */
#define CFG_SHIFT  5                                 /* bits 5-7 : notre choix (0-4 : l'arpégiateur) */
#define N_DELAY    2                                 /* Original, Tape */
#define N_REVERB   2                                 /* Original, Plate */

/* ------------------------------------------------------------------------------------------------------------ */
/* Tape                                                                                                         */
/* ------------------------------------------------------------------------------------------------------------ */
#define RING_BITS  17
#define RING       (1u << RING_BITS)                 /* 131 072 trames : 2,73 s */
#define RMASK      (RING - 1)
#define D_MIN      64                                /* retard mini, en trames */
#define D_MAX      (RING - 2048)
#define GLIDE_MAX  (24 << 8)                         /* glissement du temps : 24 trames par bloc au plus (Q8) */
#define A_LP       908000000                         /* passe-bas de lecture, ~4,2 kHz */
#define A_HP       27900000                          /* passe-haut, ~100 Hz */
#define K_WOW      5396970                           /* pleurage, 0,6 Hz (2 pi f / 1500, Q31) */
#define K_FLUT     51271000                          /* scintillement, 5,7 Hz */
#define DEPTH_WOW  (2 * (18 << 8))                   /* +-18 trames */
#define DEPTH_FLUT (2 * 150)                         /* +-0,6 trame */
#define C_16_27    1272582903                        /* 16/27 */

struct tape {
	u32 w, valid;                                    /* tête d'écriture ; trames écrites depuis la remise à zéro */
	s32 dcur, dmod;                                  /* retard (Q8, trames), lissé ; le même modulé, fin du bloc précédent */
	s32 wx, wy, fx, fy;                              /* deux oscillateurs (Q31, amplitude 1/2) */
	s32 lp[2], hp[2];
};

/* ------------------------------------------------------------------------------------------------------------ */
/* Plate                                                                                                        */
/* ------------------------------------------------------------------------------------------------------------ */
#define PBUF       16384
#define PMASK      (PBUF - 1)
/* lignes à retard (base, longueur) : celles de clouds/dsp/fx/reverb.h x 0,75 (24 kHz au lieu de 32) */
#define L_AP1      85
#define L_AP2      121
#define L_AP3      181
#define L_AP4      299
#define L_DAP1A    1241
#define L_DAP1B    1529
#define L_DEL1     2559
#define L_DAP2A    1435
#define L_DAP2B    1247
#define L_DEL2     3587
#define B_AP1      0
#define B_AP2      (B_AP1 + L_AP1 + 1)
#define B_AP3      (B_AP2 + L_AP2 + 1)
#define B_AP4      (B_AP3 + L_AP3 + 1)
#define B_DAP1A    (B_AP4 + L_AP4 + 1)
#define B_DAP1B    (B_DAP1A + L_DAP1A + 1)
#define B_DEL1     (B_DAP1B + L_DAP1B + 1)
#define B_DAP2A    (B_DEL1 + L_DEL1 + 1)
#define B_DAP2B    (B_DAP2A + L_DAP2A + 1)
#define B_DEL2     (B_DAP2B + L_DAP2B + 1)
#define KAP        1503238554                        /* diffusion 0,7 */
/* Échelle interne : 1/8 de celle de Clouds (ses valeurs dépassent 1 ; ici Q27, jamais de débordement : au pire
 * 0,05 x 2,4^4 + 0,5 = 2,2 puis x 2,4^2 = 12,4 < 16). */
#define GAIN_IN    107374182                         /* 0,05 : (G + D) / 2 x 0,05 = 0,2 (G + D) / 8 */
#define SMEAR_OFF  (15 << 7)                         /* 7,5 (Q8) */
#define SMEAR_AMP  45
#define SMEAR_WR   75
#define MOD_OFF    (3510 << 8)
#define MOD_AMP    75
#define INC_LFO1   1431656                           /* 0,5 Hz à 1 500 pas par seconde (2^32 f / 1500) */
#define INC_LFO2   858993                            /* 0,3 Hz */
#define P_CLIP     0x03ffffff                        /* +-0,5 en Q27 : +-4 chez Clouds */
#define CLEAR_STEP 2048                              /* mots effacés par bloc à la remise à zéro */

struct plate {
	u32 wp, clear;                                   /* tête d'écriture ; mots restant à effacer */
	u32 ph1, ph2;
	s32 lp1, lp2;
	s32 dec[6];                                      /* 48 -> 24 kHz : les 6 derniers échantillons mono */
	s32 up[2][3];                                    /* 24 -> 48 kHz : les 3 derniers par canal */
	u32 flush;                                       /* blocs pendant lesquels vider les tampons de la reverb d'origine */
};

struct fx {
	u32 sel_d, sel_r;                                /* algorithmes en cours */
	s32 ui_acc;                                      /* interface : crans accumulés */
	u32 ui_which;
	struct tape tape;
	struct plate plate;
};

struct fx st __attribute__((section(".data.st"))) = { 0 };
static s16 ring[RING * 2];
static s32 pbuf[PBUF];

static inline s32 sat_shl7(s32 x)
{
	return x > 0x00ffffff ? 0x7fffffff : x < -0x01000000 ? (s32)0x80000000 : (s32)((u32)x << 7);
}

static inline s32 sat_shl1(s32 x)
{
	return x > 0x3fffffff ? 0x7fffffff : x < -0x40000000 ? (s32)0x80000000 : (s32)((u32)x << 1);
}

static void tape_reset(void)
{
	struct tape *t = &st.tape;

	t->w = t->valid = 0;
	t->dcur = t->dmod = 0;
	t->wx = t->fx = 0x40000000;
	t->wy = t->fy = 0;
	t->lp[0] = t->lp[1] = t->hp[0] = t->hp[1] = 0;
}

static void plate_reset(void)
{
	struct plate *p = &st.plate;
	int i;

	p->wp = 0;
	p->clear = PBUF;
	p->ph1 = p->ph2 = 0;
	p->lp1 = p->lp2 = 0;
	for (i = 0; i < 6; i++)
		p->dec[i] = 0;
	for (i = 0; i < 3; i++)
		p->up[0][i] = p->up[1][i] = 0;
	p->flush = 560;                                  /* un tour de ses lignes les plus longues (512 blocs) */
}

/* Début de bloc (fx_hooks.S, à l'entrée du delay) : les algorithmes que demande le pattern joué. Rend celui du delay. */
u32 fx_latch(void)
{
	u8 *p = CUR_PAT;
	u32 d = 0, r = 0;

	if (PAT_OK(p)) {
		d = p[CFG_OFF] >> CFG_SHIFT;
		r = p[TRK_SIZE + CFG_OFF] >> CFG_SHIFT;
	}
	if (d >= N_DELAY)
		d = 0;
	if (r >= N_REVERB)
		r = 0;
	if (d != st.sel_d) {
		st.sel_d = d;
		if (d)
			tape_reset();
	}
	if (r != st.sel_r) {
		st.sel_r = r;
		if (r)
			plate_reset();
	}
	return d;
}

/* ---- Tape : (out, in, params) ---- */
void fx_tape(s32 *out, const s32 *in, const s16 *prm)
{
	struct tape *t = &st.tape;
	s32 time = prm[0], fbk = prm[1], tempo = TEMPO;
	s32 d, target, diff, step, dq, inc, fb;
	u32 w = t->w, *stage = DLY_STAGE;
	int n, c;

	for (n = 0; n < 64; n++)                         /* le delay d'origine ne tourne pas : sa mémoire se vide */
		stage[n] = 0;

	/* temps : celui du delay d'origine (0x4005819a), en trames */
	if (tempo < 1)
		tempo = 14400;
	if (time < 0)
		time = 0;
	d = ((((time + 256) * 48000) >> 10) * 900) / tempo;
	if (d < D_MIN)
		d = D_MIN;
	if (d > (s32)D_MAX)
		d = D_MAX;
	target = d << 8;
	if (!t->valid) {                                 /* 1er bloc : pas de glissement */
		t->dcur = target;
		t->dmod = target;
	}
	diff = target - t->dcur;
	step = diff >> 6;
	if (step > GLIDE_MAX)
		step = GLIDE_MAX;
	if (step < -GLIDE_MAX)
		step = -GLIDE_MAX;
	if (!step)
		step = diff;
	t->dcur += step;

	/* pleurage et scintillement : deux oscillateurs, un pas par bloc */
	t->wx += qmul(t->wy, K_WOW);
	t->wy -= qmul(t->wx, K_WOW);
	t->fx += qmul(t->fy, K_FLUT);
	t->fy -= qmul(t->fx, K_FLUT);
	dq = t->dmod;
	target = t->dcur + qmul(t->wx, DEPTH_WOW) + qmul(t->fx, DEPTH_FLUT);
	if (target < (D_MIN << 7))
		target = D_MIN << 7;
	inc = (target - dq) >> 5;
	t->dmod = dq + 32 * inc;

	/* réinjection : 127 -> 1,03 (au-delà de 1 : l'écho s'emballe, la saturation le tient) */
	if (fbk < 0)
		fbk = 0;
	if (fbk > 32512)
		fbk = 32512;
	fb = fbk * 66052;

	for (n = 0; n < 32; n++, dq += inc) {
		u32 i1 = (w - (u32)(dq >> 8)) & RMASK, i0 = (i1 - 1) & RMASK;
		s32 g = 256 - (dq & 255);
		int mute = (u32)(dq >> 8) + 1 > t->valid + n;    /* pas encore écrit depuis la remise à zéro */

		for (c = 0; c < 2; c++) {
			s32 a = ring[2 * i0 + c], b = ring[2 * i1 + c];
			s32 x = mute ? 0 : (s32)((u32)((a << 8) + (b - a) * g) << 7);   /* tête de lecture, demi-échelle */
			s32 y, f, h, h3;

			t->lp[c] += qmul(x - t->lp[c], A_LP);
			t->hp[c] += qmul(t->lp[c] - t->hp[c], A_HP);
			y = t->lp[c] - t->hp[c];
			out[2 * n + c] = sat_shl1(y);
			f = qmul(y, fb);
			f += f >> 5;
			if (f > 0x3fffffff)
				f = 0x3fffffff;
			if (f < -0x3fffffff)
				f = -0x3fffffff;
			h = (in[2 * n + c] >> 1) + f;            /* (entrée + réinjection) / 2 */
			/* saturation de la bande : y = v - 4/27 v^3 jusqu'à |v| = 1,5, puis +-1 ; ici v / 2 -> y / 2 */
			if (h >= 0x60000000)
				h = 0x40000000;
			else if (h <= -0x60000000)
				h = -0x40000000;
			else {
				h3 = qmul(qmul(h, h), h);
				h -= qmul(h3, C_16_27);
			}
			h >>= 15;
			ring[2 * w + c] = h > 32767 ? 32767 : h;
		}
		w = (w + 1) & RMASK;
	}
	t->w = w;
	if (t->valid < RING)
		t->valid += 32;
}

/* ---- Plate : (out, in, params) ---- */
static inline s32 lfo_val(u32 ph)                    /* 0..65536, proche d'un cosinus (3t^2 - 2t^3) */
{
	u32 t = ph >> 15;                                /* 0..131071 */

	if (t > 65535)
		t = 131071 - t;                              /* triangle 0..65535 */
	return (s32)(((t * t >> 16) * (196608 - 2 * t)) >> 16);
}

#define RD(o)      pbuf[(wp + (o)) & PMASK]
/* passe-tout de fx_engine.h : Read(d TAIL, k) ; WriteAllPass(d, -k) */
#define AP(base, len, k) do { \
	s32 r_ = RD((base) + (len) - 1); \
	acc += qmul(r_, (k)); \
	RD(base) = acc; \
	acc = r_ - qmul(acc, (k)); \
} while (0)

static inline s32 interp(u32 wp, u32 base, s32 off)  /* lecture interpolée, off en Q8 */
{
	s32 a = RD(base + (off >> 8)), b = RD(base + (off >> 8) + 1);

	return a + qmul(b - a, (off & 255) << 23);
}

static inline s32 pclip(s32 x)
{
	return x > P_CLIP ? P_CLIP : x < -P_CLIP ? -P_CLIP : x;
}

void fx_plate(s32 *out, const s32 *in, const s16 *prm)
{
	struct plate *p = &st.plate;
	s32 size = (s8)(prm[0] >> 8), tone = prm[1] >> 8;
	s32 krt, klp, lp1 = p->lp1, lp2 = p->lp2, off1, off2;
	s32 m[38];
	u32 wp = p->wp;
	int n, k;

	if (p->flush) {                                  /* la reverb d'origine ne tourne pas : sa mémoire se vide */
		u32 *a = REV_STAGE_A, *b = REV_STAGE_B;

		p->flush--;
		for (k = 0; k < 8; k++, a += 40)
			for (n = 0; n < 32; n++)
				a[n] = 0;
		for (k = 0; k < 9; k++, b += 44)
			for (n = 0; n < 32; n++)
				b[n] = 0;
	}
	if (p->clear) {                                  /* remise à zéro de la mémoire, étalée sur 8 blocs */
		s32 *q = pbuf + (PBUF - p->clear);

		for (n = 0; n < CLEAR_STEP; n++)
			q[n] = 0;
		p->clear -= CLEAR_STEP;
		for (n = 0; n < 64; n++)
			out[n] = 0;
		return;
	}
	/* Size 0..127 -> temps 0,15..0,98 (Clouds : 0,35..0,98) ; Tone 0..127 -> passe-bas 0,25..0,97 */
	if (size < 0)
		size = 0;
	if (tone < 0)
		tone = 0;
	if (tone > 127)
		tone = 127;
	krt = 322122547 + size * 14034735;
	klp = 536870912 + tone * 12174711;

	/* 48 -> 24 kHz : (G + D) / 8 en Q27, filtre demi-bande (-1 0 9 16 9 0 -1) / 32 puis x 4 */
	for (n = 0; n < 6; n++)
		m[n] = p->dec[n];
	for (n = 0; n < 32; n++)
		m[6 + n] = (in[2 * n] >> 7) + (in[2 * n + 1] >> 7);
	for (n = 0; n < 6; n++)
		p->dec[n] = m[32 + n];

	p->ph1 += INC_LFO1;
	p->ph2 += INC_LFO2;
	off1 = SMEAR_OFF + ((SMEAR_AMP * lfo_val(p->ph1)) >> 8);
	off2 = MOD_OFF + ((MOD_AMP * lfo_val(p->ph2)) >> 8);

	for (k = 0; k < 16; k++) {
		const s32 *q = m + 2 * k;
		s32 x = (16 * q[3] + 9 * (q[2] + q[4]) - (q[0] + q[6])) >> 3;
		s32 acc, apout, wl, wr;

		wp = (wp - 1) & PMASK;
		/* le 1er passe-tout est brouillé dans sa boucle */
		RD(B_AP1 + SMEAR_WR) = interp(wp, B_AP1, off1);
		acc = qmul(x, GAIN_IN);
		AP(B_AP1, L_AP1, KAP);
		AP(B_AP2, L_AP2, KAP);
		AP(B_AP3, L_AP3, KAP);
		AP(B_AP4, L_AP4, KAP);
		apout = acc;
		/* la boucle : 2 x (passe-bas, 2 passe-tout, 1 retard) */
		acc += qmul(interp(wp, B_DEL2, off2), krt);
		lp1 += qmul(acc - lp1, klp);
		acc = lp1;
		AP(B_DAP1A, L_DAP1A, -KAP);
		AP(B_DAP1B, L_DAP1B, KAP);
		acc = pclip(acc);
		RD(B_DEL1) = acc;
		wl = acc;                                    /* le « wet » de Clouds vaut 2 x 8 x cette valeur */
		acc = apout + qmul(RD(B_DEL1 + L_DEL1 - 1), krt);
		lp2 += qmul(acc - lp2, klp);
		acc = lp2;
		AP(B_DAP2A, L_DAP2A, KAP);
		AP(B_DAP2B, L_DAP2B, -KAP);
		acc = pclip(acc);
		RD(B_DEL2) = acc;
		wr = acc;

		/* 24 -> 48 kHz (demi-bande, 2 échantillons de retard) ; sortie = wet / 2 = 8 x valeur, de Q27 à Q31 : << 7 */
		for (n = 0; n < 2; n++) {
			s32 *h = p->up[n], v = n ? wr : wl, e, o;

			e = h[1];
			o = (9 * (h[1] + h[2]) - (h[0] + v)) >> 4;
			h[0] = h[1];
			h[1] = h[2];
			h[2] = v;
			out[4 * k + n] = sat_shl7(e);
			out[4 * k + 2 + n] = sat_shl7(o);
		}
	}
	p->wp = wp;
	p->lp1 = lp1;
	p->lp2 = lp2;
}

/* ------------------------------------------------------------------------------------------------------------ */
/* Interface : Settings tenue + DELAY SEND ou REVERB SEND                                                       */
/* ------------------------------------------------------------------------------------------------------------ */
#ifndef FX_HOST
#define MOD_USED     (*(volatile u32 *)TG_MOD_USED)  /* Model-TG : un accord a servi depuis l'appui sur Settings */
#define SHOW_POPUP   ((void (*)(const char *))TG_SHOW_POPUP)
#define UI_STEP      4                               /* crans d'encodeur par algorithme */

static const char *const NAME_D[N_DELAY] = { "Delay FX\nOriginal", "Delay FX\nTape" };
static const char *const NAME_R[N_REVERB] = { "Reverb FX\nOriginal", "Reverb FX\nPlate" };

/* Les données de la piste t du pattern en cours, par son objet de l'interface (comme l'arpégiateur, notes/32 §10) */
static u8 *ui_track(u32 t, void **objp)
{
	void *app = ((void *(*)(void))0x400cf866)();
	void *pat = ((void *(*)(void *))0x4000f208)(app);
	void *obj = ((void *(*)(void *, u32))0x4000cfcc)(pat, t);

	*objp = obj;
	return obj ? ((u8 *(*)(void *))(*(void ***)obj)[10])(obj) : 0;
}

/* fx_hooks.S, fx_enc_hook : Settings est tenue et le potard which (0 : DELAY SEND, 1 : REVERB SEND) a tourné.
 * ev : l'événement (+16 : crans, signés). */
void fx_ui_turn(u32 which, const s32 *ev)
{
	u8 *p = CUR_PAT, *b, *d;
	s32 v, old, n = which ? N_REVERB : N_DELAY;
	int fresh = !MOD_USED || which != st.ui_which;
	void *obj;
	u32 tag = 0x400ff5ac;

	MOD_USED = 1;                                    /* le relâchement de Settings n'ouvrira pas le menu */
	if (!PAT_OK(p))
		return;
	b = p + which * TRK_SIZE + CFG_OFF;
	old = v = *b >> CFG_SHIFT;
	if (v >= n)
		old = v = 0;
	if (fresh) {
		st.ui_acc = 0;
		st.ui_which = which;
	}
	st.ui_acc += ev[4];
	if (st.ui_acc >= UI_STEP) {
		st.ui_acc = 0;
		if (v < n - 1)
			v++;
	} else if (st.ui_acc <= -UI_STEP) {
		st.ui_acc = 0;
		if (v > 0)
			v--;
	}
	if (v != old) {
		*b = (*b & ((1 << CFG_SHIFT) - 1)) | (v << CFG_SHIFT);
		d = ui_track(which, &obj);               /* les données vues par l'interface : les mêmes, ou sa copie */
		if (d) {
			d[CFG_OFF] = (d[CFG_OFF] & ((1 << CFG_SHIFT) - 1)) | (v << CFG_SHIFT);
			((void (*)(void *, u32 *))(*(void ***)obj)[4])(obj, &tag);   /* « réglage de piste modifié » */
		}
	}
	if (v != old || fresh)
		SHOW_POPUP((which ? NAME_R : NAME_D)[v]);
}
#endif
