# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置：单文件 exe。

必须把 console.html / extract.js 作为 data 打进去——
app.py 用 sys._MEIPASS 回退查找这两个资源，缺一个界面就起不来。
testpage.html 也带上：它只是本地验证页，体积可忽略，
但能让「打包后的 exe」自己证明抓取链路可用。
"""

block_cipher = None

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('console.html', '.'),
        ('extract.js', '.'),
        ('netcap.js', '.'),
    ],
    hiddenimports=[
        'webview.platforms.edgechromium',
        'webview.platforms.winforms',
        'clr_loader',
        'pythonnet',
        'grab_url',
        'argparse',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6',
        'matplotlib', 'numpy', 'pandas', 'scipy',
        'PIL', 'pytest', 'setuptools',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='网页素材抓取器',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
