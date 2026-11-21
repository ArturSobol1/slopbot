# SlopBot

Give it a topic and it produces a finished 1080×1920 vertical video (Shorts / TikTok / Reels).

```
topic → Claude writes the script → ElevenLabs voices it → captions synced to every word
      → Claude designs 3D animations in Blender (plus real footage where it fits better)
        or gameplay + characters → final.mp4
```

## Two formats

| Format | Looks like | Visuals |
|---|---|---|
| `explainer` | A narrator over visuals, with big word-by-word captions, keywords in cyan, and the current sentence in a small box at the top | **Animated (default):** 3D shots that Claude plans, scripts, previews, reviews and refines in Blender, using free 3D models from Sketchfab, plus real footage for the lines where it's clearly better. **Footage:** real video or photos (Wikimedia Commons) and stock clips (Pixabay) only |
| `dialogue` | Two characters arguing their way through a topic, with captions in the middle | Your gameplay clip in the background, and the current speaker bouncing at the bottom |

## Setup (one time)

1. Set up script writing, using one of these:
   - **Your Claude subscription (default):** run `npm install -g @anthropic-ai/claude-code`, then run `claude` once in a terminal and log in. Scripts count against your plan's usage limits.
   - **The Claude API:** put `ANTHROPIC_API_KEY` in `.env` (https://console.anthropic.com, about 5 cents per script). If this key is set, it's used instead of the subscription.
2. Copy `.env.example` to `.env` and fill in:
   - `ELEVENLABS_API_KEY`: https://elevenlabs.io (a 60-second video uses about 1,000 characters of credit)
   - `PIXABAY_API_KEY`: sign up at https://pixabay.com, and your key appears at https://pixabay.com/api/docs/ (free; only needed for the explainer format). A `PEXELS_API_KEY` works too if you have one, but Pexels has paused new keys.
   - `SKETCHFAB_API_TOKEN`: free account at https://sketchfab.com, then Settings > Password & API (for 3D models in animated videos)
3. Animated videos need Blender 4.5: unzip the portable version from https://download.blender.org/release/Blender4.5/ into `tools/blender/` (or set `BLENDER_PATH` in `.env`).
4. For the dialogue format, put some gameplay `.mp4` files in `assets/backgrounds/`. Use footage you recorded yourself or footage that's licensed for reuse. A random section is used for each video.
5. Music is automatic: Claude picks a track that fits the video from Kevin MacLeod's royalty-free Incompetech catalog (CC BY 4.0, so it's fine on monetized videos as long as the credit line from `upload_info.txt` is in the description). It starts where the track has energy and ducks under the voice. To use your own tracks instead, put them in `assets/music/` (a random one is used) or pass `--music file.mp3`.

The Python environment is in `.venv`. If you need to rebuild it:

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

## Making videos

```
.venv\Scripts\python make_video.py explainer "why planes don't fly over the Pacific"
.venv\Scripts\python make_video.py dialogue "how credit scores work"
```

Batch mode (one topic per line):

```
.venv\Scripts\python make_video.py explainer --topics-file topics.txt
```

Each video gets its own folder in `output/` containing:
- `final.mp4`, the finished video
- `upload_info.txt`, with the title, a short description and hashtags that Claude writes for the finished video (always including #Shorts), then the credits for every 3D model, photo, clip and music track used. Paste all of it into your video description: the credits are required by the CC BY / CC BY-SA licenses.
- `youtube.json`, the same title, description, hashtags and tags as data (used by `--upload`). Edit it before uploading if you want to change anything. Uploads leave the links out of the credits (new YouTube channels can't post external links); each credit keeps the title, author, license and site. `full_description` shows exactly what gets uploaded. `youtube_upload.json` appears once the video is uploaded, with its link.
- `anim/`, everything behind the animations: Claude's plan (`plan.json`), the model choices, each review round's preview sheets and scripts (`r0/`, `r1/`...) and the final shot clips and scripts (`final/`)
- `sheets/`, the contact sheets Claude chose real footage from
- `script.json`, which you can edit and re-render with `--script output\...\script.json`

### Options

- `--script file.json`: skip Claude and use a script you wrote or edited (see `examples/`)
- `--offline`: test without ElevenLabs or a stock footage key. Uses Windows' built-in voice and a plain animated background
- `--visuals footage`: real footage, photos and stock clips only, no 3D animation (faster)
- `--speed 1.1`: voice speed, 0.7-1.2. The default 1.0 is the voice's natural pace
- `--no-music`, or `--music file.mp3` to choose the track yourself
- `--resume output\<run folder>`: finish a run that stopped partway (crash, closed window). It reuses the script, the cached voice and the shots Claude already reviewed, and skips clips that finished rendering

- `--upload`: upload the finished video to YouTube as a Short (off by default; needs the one-time setup below). Add `--privacy unlisted` or `--privacy public`; the default is `private`

## Uploading to YouTube

SlopBot can upload `final.mp4` as a YouTube Short with the title, Claude's description + hashtags, the license credits, tags, category Education, "not made for kids", and the "altered or synthetic content" AI disclosure set.

### One-time Google Cloud setup (about 10 minutes)

1. Go to https://console.cloud.google.com/projectcreate, sign in with the Google account that owns your YouTube channel, and create a project (e.g. "SlopBot").
2. Open https://console.cloud.google.com/apis/library/youtube.googleapis.com, make sure your new project is selected at the top, and click **Enable**.
3. Open https://console.cloud.google.com/auth/overview and click **Get started**:
   - App name: `SlopBot`, User support email: your email
   - Audience: **External**
   - Contact email: your email, then agree and **Create**
4. Go to **Audience** (left menu) > **Test users** > **Add users**, and add the Google account that owns the channel.
5. Go to **Clients** > **Create client**, choose Application type **Desktop app**, name it `SlopBot`, and click **Create**. Click **Download JSON** and save the file as `secrets\client_secret.json` in this folder. Keep it private: it's in `.gitignore`.
6. Test it without uploading anything:
   ```
   .venv\Scripts\python upload.py output\<some run folder> --dry-run
   ```
7. First real upload (private):
   ```
   .venv\Scripts\python upload.py output\<some run folder>
   ```
   Your browser opens. Sign in, pick the channel, and on "Google hasn't verified this app" click **Continue** (it's your own app). Allow uploads. The sign-in is saved in `secrets\youtube_token.json`, so later uploads don't ask again.

While the app's publishing status is **Testing**, Google expires the saved sign-in after 7 days, and the browser opens again on the next upload. To stop that, go to **Audience** and click **Publish app**. You don't need to submit it for verification for your own use; you just keep seeing the "unverified app" warning when you sign in.

### Uploading

```
.venv\Scripts\python make_video.py explainer "why the sky is blue" --upload                    (private)
.venv\Scripts\python upload.py output\<run folder> --privacy unlisted                           (an existing video)
.venv\Scripts\python upload.py output\<run folder> --dry-run                                    (show what would be sent)
.venv\Scripts\python upload.py output\<run folder> --describe-only                              (just write youtube.json)
```

- The video link is printed when it's done and saved in `youtube_upload.json`. Uploading the same folder twice is refused unless you add `--force`.
- If an upload fails, the video stays in its folder; run `upload.py` on it again.
- Uploads use the YouTube Data API's own upload quota: 100 uploads per day per Google Cloud project, free. You only need the YouTube API audit (https://support.google.com/youtube/contact/yt_api_form) to raise that limit.
- Private and unlisted videos can be switched to public later in YouTube Studio.

### How the animations are made

1. **Plan:** Claude decides for each line whether it gets a 3D animation (most lines) or real footage, picks one visual style and color palette, and lists the 3D models it needs.
2. **Models:** each model is searched on Sketchfab (only licenses that allow monetized use), and Claude picks from the thumbnails.
3. **Scripts:** Claude writes a short Blender script per shot with the toolkit in `tools/sb_blender/sb.py` (models, shapes, 3D text, labels, arrows, rings, counters, camera moves). Scripts can't import anything or touch files.
4. **Review:** every shot gets a quick 3-frame preview. Claude checks framing, model orientation, readability and errors, and rewrites what's off, for up to 2 rounds.
5. **Render:** final 1080×1920 clips, one per line. A shot that still fails falls back to real footage.

An animated video takes roughly 20-30 minutes and several Claude requests (plan, models, scripts, reviews), which count against your Claude plan's usage limits.

## Customizing

- **Characters:** edit `assets/characters/characters.json` to change the names, personalities (Claude writes the dialogue from these), ElevenLabs voice IDs and caption highlight colors. Replace `pip.png` / `dunk.png` with your own art (transparent PNGs, roughly 600×800).
- **Narrator voice:** set `NARRATOR_VOICE_ID` in `.env`. You can copy any voice ID from the ElevenLabs Voice Library.
- **Script style:** the writing rules are at the top of `slopbot/script.py`.
- **Caption look:** font, sizes and colors are in `slopbot/captions.py` (the `Style:` lines and `max_chars`).

## Notes

- Check the facts in each script before you upload. Claude is told not to invent numbers, but it can still get things wrong.
- YouTube can demonetize channels that post mass-produced, repetitive content. Real topics, accurate facts and varied formats help.
- Only use characters, voices and footage you have the rights to. Don't clone a real person's voice without their permission.
