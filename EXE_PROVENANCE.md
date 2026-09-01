# Historical EXE provenance

Two historical executables were compared:

- `ZondaPIT.exe` — PE timestamp `2026-03-08 11:01:52 UTC` (`12:01:52 CET`), SHA-256 `b9f05032cb586aa70c906d289416c876e9703727a4e426ab742e2ccb064a9851`.
- `kalkulatorZONDA.exe` — PE timestamp `2026-03-08 14:15:35 UTC` (`15:15:35 CET`), SHA-256 `e5d8b628c6e4c0cf8359691c2347e1d84f258ce41c76bd6349714768ab704181`.

The later recovered Python source (mtime `2026-03-08 15:08:36`) strongly matches the later executable: 57 named functions/classes with matching first-line positions and the same later Airtable telemetry feature.

The smaller `ZondaPIT.exe` is a pre-Airtable code generation. Its embedded script has 50 named functions/classes and lacks the seven later code objects associated with file-signature/Airtable/year-from-filename additions.

`scripts/kalkulatorZONDA.py` in this repository is a reconstructed pre-Airtable source based on the recovered later source, with those later additions removed. It has 50 named functions/classes, matching the structural count observed in the historical small executable.

This is strong structural provenance evidence, but not cryptographic proof of byte-for-byte identity with the lost original `.py` source. New releases are built from Git commits by GitHub Actions and publish both SHA-256 and the build commit.
