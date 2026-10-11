/* Référence de la preuve de MOD (notes/57, tools/emu/test_mod.py) : tools/machines/mod/mod.c compilé pour
 * l'ordinateur (MOD_HOST : le tempo et l'heure musicale sont des variables). Entrée, par bloc (petit-boutiste) :
 * tempo (int32), heure musicale (uint32), trigs (uint32), puis les 6 pistes de P (6 x 33 mots int16, P + 14).
 * Sortie : les 6 pistes après mod_pre, les 6 sorties des générateurs (int32), puis les 6 pistes après mod_post. */
#include <stdio.h>
#include <string.h>

int host_tempo;
unsigned int host_time;

#include "../machines/mod/mod.c"

int main(void)
{
	static short P[7 + 6 * 33 + 8];
	int head[3];
	int t;
	for (;;) {
		if (fread(head, 4, 3, stdin) != 3)
			return 0;
		if (fread(P + 7, 2, 6 * 33, stdin) != 6 * 33)
			return 1;
		host_tempo = head[0];
		host_time = (unsigned int)head[1];
		mod_pre(P, (unsigned int)head[2]);
		fwrite(P + 7, 2, 6 * 33, stdout);
		for (t = 0; t < 6; t++)
			fwrite(&mod_st.g[t].out, 4, 1, stdout);
		mod_post(P);
		fwrite(P + 7, 2, 6 * 33, stdout);
		fflush(stdout);
	}
}
