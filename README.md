# MoMo

MoMo plays your MKV and MP4 files and takes the canned laughter out. Other files are left alone.

It is for people who are tired of a studio audience laughing for them, and for people who just want to hear the show without the crowd. The jokes stay. The "ha ha ha, please clap" does not.

It's not perfect yet, but I'm trying.

## Install

Download the latest build from the [Releases](https://github.com/mhsnprvr/momo/releases) page.

**Mac:** open `MoMo.dmg`, drag MoMo to Applications, and replace the old copy if one is already there.

The app is not signed with an Apple certificate. The first time you open it, macOS will act like you have invited a stranger in. Right-click MoMo, choose Open, and confirm. After that it opens like a normal app.

**Windows:** run `MoMo-Setup.exe`. The installer is not signed either, so Windows may say it protected your PC. Click **More info**, then **Run anyway**. MoMo then shows up in the Start menu and under **Open with** for MKV and MP4 files. To clean a pile of videos at once, select them all and drag them onto the MoMo shortcut.

## Watch one episode

Open a video with MoMo. Or start MoMo on its own: it opens an empty player. Drop a video on it, or click **Choose videos**.

**Play now** starts in a few seconds. The sound is a little rougher. You may hear a faint seam, or a bit of crowd on a long laugh. Good when you just want the episode to start.

**Clean first** waits until the whole episode is cleaned, so playback does not stop to catch up. You then get a slider:

- **Faster** finishes sooner. Long laughs may sound slightly rough.
- **Smoothest** is the cleanest. It takes the longest.

The slider starts on the smooth end. Drag left if you would rather get on with your life.

While it cleans, a card in the middle of the picture shows how far along it is. Press **L** anytime to hear the original soundtrack, laugh track and all. Press **L** again to send the audience home. The window stays on top, so your other windows cannot sneak back in and talk over the show.

## Clean a pile of them

Select more than one MKV or MP4, or drop several on the empty player. MoMo opens a list instead of the player.

The slider starts at **Faster**. Drag it toward **Smoothest** if you want the better pass and you are willing to wait. Press **OK**.

MoMo cleans one file at a time and saves each one beside the original:

`Episode.mkv` becomes `no_crowd_Episode.mkv`

An existing `no_crowd_` copy is replaced. Each row has its own progress bar and a **Cancel** button. Cancel skips that file and moves on. Close the window to stop the rest after the current piece of audio finishes.

## Build it yourself

Every push makes GitHub Actions build the Mac and Windows versions; download them from the run on the Actions tab. A newer push to the same branch cancels the build still running for the older one. Pushing a tag like `v1.0.1` also attaches both builds to a new release.

To build locally on a Mac, with [ffmpeg](https://ffmpeg.org/) and [mpv](https://mpv.io/) installed:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
./packaging/build_dmg.sh
```

The installer is written to `dist/MoMo.dmg`. The crowd model is packed inside the app, so the first launch does not have to download it.

On Windows, install [Inno Setup](https://jrsoftware.org/isinfo.php) and [7-Zip](https://www.7-zip.org/), then in PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.\packaging\build_windows.ps1
```

The script downloads mpv and ffmpeg itself and writes the installer to `dist\MoMo-Setup.exe`.
