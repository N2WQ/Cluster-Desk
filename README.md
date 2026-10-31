# Cluster Desk by N2WQ

Cluster Desk is a standalone DX cluster client inspired by VE7CC / CC User. It connects to CC Cluster and DXSpider nodes, displays and searches spots, and provides node filter controls. The interface supports light and dark themes.

**Beta:** This is a work in progress, not yet a complete VE7CC replacement. Use it for evaluation alongside your usual operating tools.

## Download

Download the Windows ZIP from [Releases](../../releases), extract it, and open `ClusterDesk.exe`. Python is not required. See the included `START-HERE.txt` and `BETA-GUIDE.txt` for instructions and known limitations.

Experimental macOS downloads are also available for Apple silicon and Intel. They are not Apple-notarized, and the graphical app and live cluster workflows have not yet been tested on a Mac; macOS may block the first launch. Choose the ZIP for your Mac's processor and read the included guide.

## Run from source

Use Python 3 with Tkinter on Windows and run `python app.py`. The optional serial logger feature requires `pyserial`. Source mode stores settings in `settings.json` beside the source; this file is excluded from version control.

## Build the Windows executable

From PowerShell in this directory:

```powershell
python -m venv .build-venv
.\.build-venv\Scripts\python.exe -m pip install -r build-requirements.txt
.\build-windows.ps1
```

The executable is written to `dist\ClusterDesk.exe`.

## Tests

Run focused tests with `python -m unittest test_spot_submission`, for example. Some reference-comparison tests require local VE7CC or DXSpider reference files that are not included here. A full local run of beta 183 had two Enter-key UI test failures; these tests passed separately, and the cause has not been established.
