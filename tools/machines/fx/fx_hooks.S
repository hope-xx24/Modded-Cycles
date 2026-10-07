/* Accroches de FX (notes/44), liées avec fx.c après l'image (tools/gen_fx.py). Les écritures sur l'OS sont dans le
 * générateur (HOOKS). */
	.globl	fx_delay_entry, fx_reverb_entry, fx_enc_hook

	.section .text.hooks, "ax"

/* 0x40057488 (8 o) : jmp fx_delay_entry ; nop, à la place de lea -48(sp),sp ; movem.l d2-d7/a2-a6,(sp), le début du
 * delay de l'OS. 4(sp) = sortie, 8(sp) = entrée, 12(sp) = paramètres. fx_latch (C) lit le choix du pattern et rend
 * l'algorithme du delay ; d0-d1/a0-a1 sont libres ici (le delay les charge avant de les lire). Original : les deux
 * instructions remplacées, puis la suite du delay. Sinon notre delay, avec les mêmes arguments ; il revient chez
 * l'appelant, accumulateurs vides comme le delay de l'OS. */
fx_delay_entry:
	jsr	fx_latch
	tst.l	%d0
	bne.s	1f
	lea	-48(%sp), %sp
	movem.l	%d2-%d7/%a2-%a6, (%sp)
	jmp	0x40057490
1:	move.l	#0xa0, %macsr
	jmp	fx_tape

/* 0x400579c4 (8 o) : jmp fx_reverb_entry ; nop, à la place de lea -52(sp),sp ; movem.l d2-d7/a2-a6,(sp), le début de
 * la reverb de l'OS. Le delay est toujours appelé avant elle dans le bloc : st.sel_r est à jour. */
fx_reverb_entry:
	tst.l	st + 4
	bne.s	1f
	lea	-52(%sp), %sp
	movem.l	%d2-%d7/%a2-%a6, (%sp)
	jmp	0x400579cc
1:	move.l	#0xa0, %macsr
	jmp	fx_plate

/* 0x400081ce (6 o) : jsr fx_enc_hook à la place de jsr gm_enc_gate (Model-TG, lui-même à la place de
 * jsr 0x40006158) : le point de passage de tous les événements d'encodeur, (racine, événement), résultat inutilisé.
 * Settings tenue (set_held de Model-TG) et encodeur 8 (DELAY SEND) ou 9 (REVERB SEND) : l'événement est pour nous,
 * personne d'autre ne le reçoit. Sinon, comme avant. */
fx_enc_hook:
	tst.l	TG_SET_HELD
	beq.s	2f
	move.l	8(%sp), %a0
	move.l	12(%a0), %d0
	subq.l	#8, %d0
	moveq	#1, %d1
	cmp.l	%d0, %d1
	bcs.s	2f
	move.l	%a0, -(%sp)
	move.l	%d0, -(%sp)
	jsr	fx_ui_turn
	addq.l	#8, %sp
	moveq	#1, %d0
	rts
2:	jmp	TG_GM_ENC_GATE
