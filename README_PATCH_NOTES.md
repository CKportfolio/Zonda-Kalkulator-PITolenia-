# Source recovery note

The original Python file used to build the historical small `ZondaPIT.exe` was not found directly.

A later source revision and two historical PyInstaller executables were available. Binary/source comparison shows that the small executable predates the Airtable telemetry revision. The repository source is therefore reconstructed from the later source by removing the seven later code objects and their integration points while preserving the PIT calculation pipeline.

Automated tests cover parsing, OPS/TRD recognition, pairing, fee assignment and valuation, NBP fallback behavior, market conversion graphs, and a deterministic end-to-end synthetic annual calculation.
