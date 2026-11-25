# Test audio

Tiny Ogg files for `tests/test_loop_editor.py`. They were made with ffmpeg (libvorbis), for example:

```
ffmpeg -f lavfi -i "sine=frequency=440:duration=2:sample_rate=44100" -ac 2 -c:a libvorbis -q:a 2 \
       -metadata LOOPSTART=22050 -metadata LOOPLENGTH=44100 loop_tagged.ogg
```

| File | What it covers |
|---|---|
| `loop_tagged.ogg` | 44100 Hz stereo, LOOPSTART=22050, LOOPLENGTH=44100 |
| `no_tags.ogg` | 22050 Hz mono, no loop tags |
| `lower_case.ogg` | 48000 Hz, tags written in lower case (`loopstart`, `looplength`) |
| `start_only.ogg` | LOOPSTART without LOOPLENGTH |
| `big_comment.ogg` | a 70,000-byte comment, so the comment block spans many Ogg pages |
| `opus.ogg` | Ogg Opus, which must be refused with a clear message |
