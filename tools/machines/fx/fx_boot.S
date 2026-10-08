/* Crochet de démarrage de FX (notes/44 §8), assemblé par tools/gen_fx.py juste après l'image, là où finit le tweak
 * précédent, et exécuté sur place, une fois : appelé par le jsr de 0x40000530, à la place du crochet précédent (celui
 * de MACRO, sinon boot_extra_hook de Model-TG), où l'on finit (FX_PREV). Il recopie le code de FX, qui le suit dans
 * l'image, vers FX_DST (0x46750000, dans le dernier Mo de la zone de Model-TG) : ce qui suit l'image est remis à zéro
 * par le crochet de Model-TG juste après (son BSS), puis repris par le cache du système de fichiers (notes/31 §1).
 * Les caches ne sont pas encore en service (0x40000542). Seuls d0, a0 et a1 servent, comme dans stub.S. */
	.text
	.globl	fx_boot
fx_boot:
	lea	fx_blob(%pc), %a0
	lea	FX_DST, %a1
	move.l	#FX_LONGS, %d0
1:	move.l	(%a0)+, (%a1)+
	subq.l	#1, %d0
	bne.s	1b
	jmp	FX_PREV
	.balign	4
fx_blob:
