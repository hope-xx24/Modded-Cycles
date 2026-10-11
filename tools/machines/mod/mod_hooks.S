/* Accroche de MOD (notes/57) : l'appel de la boucle des voix par la fonction audio (0x4005981e : jsr vers la boucle,
 * 0x400a7d4a, ou vers le detour de Model-TG) devient jsr mod_vl. MOD_NEXT : la cible d'avant (tools/gen_mod.py).
 * Arguments de l'appel, laisses tels quels : (0x80001858, P, trigs, a1) ; d0-d1/a0-a1 libres, comme pour tout
 * appel de l'OS. */
	.text
	.globl	mod_vl
mod_vl:
	move.l	12(%sp), -(%sp)		/* trigs */
	move.l	12(%sp), -(%sp)		/* P */
	jsr	mod_pre
	addq.l	#8, %sp
	move.l	16(%sp), -(%sp)		/* les 4 arguments, dans le meme ordre */
	move.l	16(%sp), -(%sp)
	move.l	16(%sp), -(%sp)
	move.l	16(%sp), -(%sp)
	jsr	MOD_NEXT
	lea	16(%sp), %sp
	move.l	8(%sp), -(%sp)		/* P */
	jsr	mod_post
	addq.l	#4, %sp
	rts
