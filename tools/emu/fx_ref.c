/* Référence de la preuve de FX (notes/44, tools/emu/test_fx.py) : tools/machines/fx/fx.c compilé pour l'ordinateur
 * (FX_HOST : l'arithmétique exacte de l'EMAC, comme emac.py), piloté bloc par bloc.
 *
 * Entrée (stdin), par bloc : 5 x int32 (algorithme du delay, de la reverb, paramètre 1, paramètre 2, tempo) puis
 * 64 x int32 (32 trames stéréo). Sortie (stdout) : 64 x int32 du delay puis 64 x int32 de la reverb (l'effet
 * « Original » rend des zéros : il n'est pas ici). Les deux effets reçoivent la même entrée et les mêmes paramètres
 * (mots 8.8), comme le test les donne au firmware. Entiers de la machine hôte (petit-boutiste). */
#include <stdio.h>
#include <string.h>

#define FX_HOST
#include "../machines/fx/fx.c"

u8 *host_cur_pat;
s32 host_tempo;
u32 *host_dly_stage;
u32 host_rev_stage[8 * 40 + 9 * 44];

int main(void)
{
	static u8 pat[2 * TRK_SIZE];
	static u32 stage[64];
	s32 hdr[5], in[64], out[128];
	s16 prm[2];

	host_cur_pat = pat;
	host_dly_stage = stage;
	while (fread(hdr, 4, 5, stdin) == 5 && fread(in, 4, 64, stdin) == 64) {
		pat[CFG_OFF] = (u8)(hdr[0] << CFG_SHIFT);
		pat[TRK_SIZE + CFG_OFF] = (u8)(hdr[1] << CFG_SHIFT);
		prm[0] = (s16)hdr[2];
		prm[1] = (s16)hdr[3];
		host_tempo = hdr[4];
		memset(out, 0, sizeof out);
		if (fx_latch())
			fx_tape(out, in, prm);
		if (st.sel_r)
			fx_plate(out + 64, in, prm);
		fwrite(out, 4, 128, stdout);
		fflush(stdout);
	}
	return 0;
}
