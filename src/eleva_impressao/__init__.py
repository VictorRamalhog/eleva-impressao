"""Eleva Impressão — visualizador e impressor de etiquetas ZPL, local.

O que existe aqui e por quê:

- ``render``  — motor ZPL → bitmap. É o coração: **o preview e o que vai para a
  impressora saem da mesma função**, então "o que eu vejo" e "o que sai no
  papel" não podem divergir por construção.
- ``barras``  — geradores de código de barras (Code 128, 39, 2/5, EAN, QR).
- ``labelary``— segundo motor, online, para conferir fidelidade.
- ``formatos``— tamanhos de etiqueta, editáveis pela operadora.
- ``windows`` — as três formas de falar com a impressora térmica.
- ``app``     — a UI (FastAPI + HTML servido em 127.0.0.1).
- ``desktop`` — a janela; é o que vira ``Eleva Impressão.exe``.
"""
