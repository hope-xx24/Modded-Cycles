/*
 * MOD : machine ajoutee du Model:Cycles (OS 1.13) qui ne sonne pas : elle module les potards de machine d'une
 * piste (la sienne ou une autre), comme un module d'un systeme modulaire (notes/57). Compilee avec MACRO, dans
 * la meme charge utile (tools/gen_mod.py).
 *
 * Ses potards :
 *   COLOR   Rate  (RATE) : 0..63 libre, de 0,02 Hz a 40 Hz (exponentiel) ; 64..127 cale sur le tempo, 16 durees
 *                         de 8 mesures a la double croche de triolet (4 crans par duree, MOD_LEN6)
 *   SHAPE   Wave  (WAVE) : 0 sinus, 1 triangle, 2 carre, 3 dent de scie montante, 4 descendante,
 *                         5 aleatoire tenu (S&H), 6 aleatoire lisse
 *   SWEEP   Depth (DPTH) : 64 = rien ; au-dessus, la modulation ; en dessous, la meme inversee (+-128 a fond)
 *   CONTOUR Track (TRK)  : piste visee, 1..6 ; 0 = aucune
 *   DECAY   Param (PARM) : potard vise : 0 PITCH, 1 DECAY, 2 COLOR, 3 SHAPE, 4 SWEEP, 5 CONTOUR, 6 FINE
 * Un trig de la piste MOD remet la phase a zero et tire une nouvelle valeur aleatoire. Le LFO d'origine de la
 * piste MOD module ses propres potards (Rate, Depth...) ; une piste MOD peut en viser une autre.
 *
 * Contrat (notes/57 §2) : la fonction audio (0x40059872) calcule les parametres lisses des 6 pistes (p-locks et LFO
 * compris) dans une structure P (piste t a P + 14 + 66 t, mots 8.8 ; le meme p que recoivent les update des
 * machines), puis appelle la boucle des voix (0x400a7d4a, ou le detour de Model-TG). mod_vl (mod_hooks.S) prend cet
 * appel : mod_pre garde les potards des 6 pistes, calcule chaque piste MOD dans l'ordre et ajoute sa sortie au potard
 * vise ; puis la boucle des voix tourne ; puis mod_post remet les potards gardes (la structure sert de memoire au
 * lissage de l'OS : sans cela, la modulation s'y accumulerait).
 */
typedef short s16;
typedef int s32;
typedef unsigned int u32;
typedef unsigned char u8;

#ifndef MOD_INDEX
#define MOD_INDEX 8                                  /* index de la machine MOD (Model-TG + MACRO + MOD) */
#endif
#ifdef MOD_HOST                                      /* la reference de la preuve (tools/emu/mod_ref.c) */
extern s32 host_tempo;
extern u32 host_time;
#define TEMPO      host_tempo
#define SONG_TIME  host_time
#else
#define TEMPO      (*(volatile const s32 *)0x40149310)   /* 1/120 de BPM */
#define SONG_TIME  (*(volatile const u32 *)0x8000184c)   /* heure musicale : + 2 x tempo par bloc */
#endif
#define CLOCK      900000u                           /* 1/6 de double croche (une impulsion d'horloge MIDI) */

#define NTRK       6
#define TSTRIDE    66                                /* octets par piste dans P */
#define TRACK(P, t) ((s16 *)((char *)(P) + 14 + TSTRIDE * (t)))
/* mots de la piste (index de mot = l'emplacement des descripteurs de l'OS) */
#define S_MACHINE  9                                 /* octet fort : la machine choisie */
#define S_PITCH    10
#define S_COLOR    11
#define S_SHAPE    12
#define S_SWEEP    13
#define S_CONTOUR  14
#define S_FINE     17
#define S_DECAY    18
#define NPARAM     7

static const u8 PARAM_SLOT[NPARAM] = { S_PITCH, S_DECAY, S_COLOR, S_SHAPE, S_SWEEP, S_CONTOUR, S_FINE };
/* bornes des potards vises (8.8) : PITCH de -24 a +24 demi-tons (0x2800..0x5800), les autres 0..127 */
static const s16 PARAM_MIN[NPARAM] = { 0x2800, 0, 0, 0, 0, 0, 0 };
static const s16 PARAM_MAX[NPARAM] = { 0x5800, 0x7f00, 0x7f00, 0x7f00, 0x7f00, 0x7f00, 0x7f00 };

/* Rate libre : pas de phase par bloc (1 500 blocs par seconde), 0,02 Hz x 2000^(i/63), i = 0..63 */
static const u32 FREE_INC[64] = {
	57266, 64609, 72894, 82241, 92787, 104685, 118109, 133254,
	150341, 169619, 191369, 215908, 243594, 274830, 310071, 349831,
	394689, 445300, 502400, 566823, 639506, 721509, 814028, 918410,
	1036177, 1169045, 1318950, 1488078, 1678893, 1894176, 2137065, 2411098,
	2720271, 3069089, 3462636, 3906647, 4407593, 4972774, 5610429, 6329849,
	7141520, 8057271, 9090447, 10256107, 11571239, 13055009, 14729041, 16617733,
	18748610, 21152727, 23865123, 26925326, 30377937, 34273273, 38668104, 43626481,
	49220666, 55532188, 62653031, 70686973, 79751101, 89977514, 101515251, 114532461,
};
/* Rate cale : la duree d'un cycle en 1/6 de double croche : 8, 4, 2 mesures, 1 mesure, blanche pointee, blanche,
 * noire pointee, blanche de triolet, noire, croche pointee, noire de triolet, croche, double croche pointee,
 * croche de triolet, double croche, double croche de triolet */
static const u32 MOD_LEN6[16] = { 768, 384, 192, 96, 72, 48, 36, 32, 24, 18, 16, 12, 9, 8, 6, 4 };

struct gen {
	u32 ph;                                          /* phase (Q32) */
	u32 pos;                                         /* cale : position dans le cycle, en heure musicale */
	u32 len;                                         /* cale : duree du cycle en cours */
	u32 rnd;                                         /* generateur aleatoire */
	s32 prev, held;                                  /* aleatoire : valeur precedente, valeur tiree (Q15) */
	s32 out;                                         /* derniere sortie (Q15), pour la preuve */
};

struct mod {
	u32 last_time;                                   /* heure musicale du bloc precedent */
	u32 started;
	struct gen g[NTRK];
	s16 saved[NTRK][NPARAM];                         /* potards gardes par mod_pre, remis par mod_post */
	u32 active;                                      /* une piste MOD au moins : il faut remettre */
};

struct mod mod_st;

static inline s32 knob(const s16 *tr, int slot, s32 max)   /* valeur entiere d'un potard, bornee a 0..max */
{
	s32 v = tr[slot] >> 8;
	return v < 0 ? 0 : (v > max ? max : v);
}

static inline u32 next_rnd(struct gen *g)
{
	g->rnd = g->rnd * 1664525u + 1013904223u;
	return g->rnd;
}

/* sinus d'une phase Q32 en Q15 : parabole 4x(1-|x|), corrigee (erreur < 0,1 %) */
static s32 sine(u32 ph)
{
	s32 x = (s32)(ph >> 16) - 32768;                 /* -1..1 en Q15, x = 0 en phase 1/2 */
	s32 ax = x < 0 ? -x : x;
	s32 y = (x * (32768 - ax)) >> 13;                 /* 4 x (1 - |x|) : -1..1 en Q15 */
	s32 ay;
	y = y > 32767 ? 32767 : (y < -32767 ? -32767 : y);
	ay = y < 0 ? -y : y;
	y = (y * (25395 + ((7373 * ay) >> 15))) >> 15;   /* y (0,775 + 0,225 |y|) */
	return -y;                                       /* phase 0 -> 0, 1/4 -> +1 */
}

/* phase Q32 de pos dans un cycle de c (pos < c < 2^30) : division bit a bit, 16 bits */
static u32 frac16(u32 pos, u32 c)
{
	u32 q = 0;
	int i;
	for (i = 0; i < 16; i++) {
		pos <<= 1;
		q <<= 1;
		if (pos >= c) {
			pos -= c;
			q |= 1;
		}
	}
	return q << 16;
}

static s32 wave(struct gen *g, s32 shape, u32 ph, int wrapped)
{
	s32 x = (s32)(ph >> 16);                         /* 0..65535 */
	if (wrapped && shape >= 5) {                     /* aleatoire : une valeur par cycle */
		g->prev = g->held;
		g->held = (s32)next_rnd(g) >> 16;
	}
	switch (shape) {
	case 0:
		return sine(ph);
	case 1:                                          /* triangle : 0 -> +1 -> 0 -> -1 -> 0 */
		x = (x + 16384) & 0xffff;
		return x < 32768 ? 2 * x - 32768 : 32767 - 2 * (x - 32768);
	case 2:
		return x < 32768 ? 32767 : -32768;
	case 3:
		return x - 32768;
	case 4:
		return 32767 - x;
	case 5:
		return g->held;
	default:                                         /* lisse : de la valeur precedente a la nouvelle */
		return g->prev + (((g->held - g->prev) * (x >> 1)) >> 15);
	}
}

/* Une piste MOD : met a jour son generateur et renvoie sa sortie (Q15). */
static s32 run_gen(struct gen *g, const s16 *tr, int trig, u32 delta)
{
	s32 rate = knob(tr, S_COLOR, 127), shape = knob(tr, S_SHAPE, 6);
	u32 old = g->ph;
	int wrapped;

	if (rate < 64) {                                 /* libre */
		g->ph = trig ? 0 : g->ph + FREE_INC[rate];
		wrapped = trig || g->ph < old;
	} else {                                         /* cale sur l'heure musicale */
		u32 len = MOD_LEN6[(rate - 64) >> 2] * CLOCK;
		if (len != g->len) {
			g->len = len;
			g->pos %= len;
		}
		if (trig) {
			g->pos = 0;
		} else {
			g->pos += delta;
			while (g->pos >= len)
				g->pos -= len;
		}
		g->ph = frac16(g->pos, len);
		wrapped = trig || g->ph < old;
	}
	g->out = wave(g, shape, g->ph, wrapped);
	return g->out;
}

/* avant la boucle des voix : P = les parametres lisses des 6 pistes ; trigs = un bit par piste (trig de ce bloc) */
void mod_pre(s16 *P, u32 trigs)
{
	struct mod *m = &mod_st;
	u32 now = SONG_TIME, delta;
	int t, k;

	/* heure musicale : ce qu'elle a avance ; a l'arret (rien) on avance au tempo, apres un saut (depart, retour
	 * en arriere) on recale chaque cycle sur elle */
	delta = now - m->last_time;
	m->last_time = now;
	if (!m->started) {
		m->started = 1;
		for (t = 0; t < NTRK; t++)
			m->g[t].rnd = 0x9e3779b9u * (u32)(t + 1);
		delta = 0;
	}
	if (delta == 0) {
		s32 tp = TEMPO;
		delta = tp > 0 ? 2 * (u32)tp : 0;
	} else if (delta > 64u * 65536u) {               /* plus de 32 blocs a 546 BPM : un saut */
		for (t = 0; t < NTRK; t++)
			if (m->g[t].len)
				m->g[t].pos = now % m->g[t].len;
		delta = 0;
	}

	m->active = 0;
	for (t = 0; t < NTRK; t++) {
		s16 *tr = TRACK(P, t);
		if ((u8)((u32)(unsigned short)tr[S_MACHINE] >> 8) != MOD_INDEX)   /* octet fort : la machine */
			continue;
		if (!m->active) {                            /* 1re piste MOD de ce bloc : garder les potards */
			int u;
			for (u = 0; u < NTRK; u++)
				for (k = 0; k < NPARAM; k++)
					m->saved[u][k] = TRACK(P, u)[PARAM_SLOT[k]];
			m->active = 1;
		}
		{
			s32 w = run_gen(&m->g[t], tr, (trigs >> t) & 1, delta);
			s32 depth = knob(tr, S_SWEEP, 127) - 64;
			s32 dst = knob(tr, S_CONTOUR, NTRK);
			s32 prm = knob(tr, S_DECAY, NPARAM - 1);
			s32 off, v;
			s16 *d;
			if (!dst || !depth)
				continue;
			off = (depth * w) >> 6;                  /* 8.8 : +-128 a fond */
			if (prm == 0)
				off >>= 2;                           /* PITCH : +-32 demi-tons a fond */
			d = TRACK(P, dst - 1) + PARAM_SLOT[prm];
			v = *d + off;
			v = v < PARAM_MIN[prm] ? PARAM_MIN[prm] : (v > PARAM_MAX[prm] ? PARAM_MAX[prm] : v);
			*d = (s16)v;
		}
	}
}

/* apres la boucle des voix : remettre les potards gardes */
void mod_post(s16 *P)
{
	struct mod *m = &mod_st;
	int u, k;
	if (!m->active)
		return;
	for (u = 0; u < NTRK; u++)
		for (k = 0; k < NPARAM; k++)
			TRACK(P, u)[PARAM_SLOT[k]] = m->saved[u][k];
	m->active = 0;
}

/* la machine elle-meme ne sonne pas (le detour de la boucle des voix l'envoie ici, sans l'etage d'ampli) */
void mod_update(s32 pmod, char *v, const char *p)
{
	(void)pmod;
	(void)v;
	(void)p;
}

void mod_render(s32 *out, char *v)
{
	int n;
	(void)v;
	for (n = 0; n < 32; n++)
		out[n] = 0;
}
