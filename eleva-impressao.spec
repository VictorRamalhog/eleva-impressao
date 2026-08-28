# Receita do PyInstaller para "Eleva Impressão.exe".
#
# Rodar:  uv run pyinstaller eleva-impressao.spec --noconfirm
# Sai em: dist/Eleva Impressao/Eleva Impressao.exe
#
# Duas coisas que este arquivo resolve e que passam despercebidas:
#
# 1. A pasta ``web`` (HTML, CSS, JS e a logo) não é código, então o PyInstaller
#    não a encontraria sozinho. Vai em ``datas`` com o nome
#    ``eleva_impressao_web``, que é exatamente o que ``app.recursos()`` procura
#    quando o programa está congelado.
# 2. ``win32print``/``win32ui`` entram por ``import`` dentro de função, para o
#    módulo continuar importável fora do Windows — e import dentro de função o
#    analisador estático não vê. Por isso estão listados à mão.

from pathlib import Path

RAIZ = Path(SPECPATH)
PACOTE = RAIZ / "src" / "eleva_impressao"

a = Analysis(
    [str(PACOTE / "desktop.py")],
    pathex=[str(RAIZ / "src")],
    binaries=[],
    datas=[(str(PACOTE / "web"), "eleva_impressao_web")],
    hiddenimports=[
        # impressão no Windows
        "win32print",
        "win32ui",
        "win32con",
        "PIL.ImageWin",
        # servidor
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.loops.asyncio",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan.on",
        # a janela
        "webview",
        "webview.platforms.edgechromium",
        "webview.platforms.winforms",
        "clr_loader",
        # QR
        "segno",
    ],
    hookspath=[],
    runtime_hooks=[],
    # O programa de impressão não precisa do resto do Eleva nem do maquinário
    # de teste; fora daqui, o executável dobra de tamanho à toa.
    excludes=[
        "tkinter",
        "pytest",
        "respx",
        "coverage",
        "apscheduler",
        "typer",
        "rich",
        "pypdf",
        "sqlite3",
        "matplotlib",
        "numpy",
        "IPython",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Eleva Impressao",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # Sem console: é um aplicativo de janela. Se a janela não puder abrir,
    # ``desktop.py`` avisa por caixa de mensagem e cai no navegador.
    console=False,
    disable_windowed_traceback=False,
    icon=str(RAIZ / "src" / "eleva_impressao" / "web" / "eleva.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Eleva Impressao",
)
