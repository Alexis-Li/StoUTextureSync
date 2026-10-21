# Route A: publish source images and rely on UE Auto Reimport (DEC-01)

Decided by the user on 2026-10-19 in
[M0](https://github.com/Alexis-Li/StoUTextureSync/issues/2), from the evidence
in [m0-host-validation](../history/m0-host-validation.md). It confirms the
initial direction of [ADR 0001](0001-file-based-sync.md).

Painter publishes each validated output by writing a temporary file beside the
target and atomically replacing it; the official Unreal Editor 5.7.4 updates the
existing Texture through Auto Reimport. In M0 this reimported only complete
files, kept texture settings and material references, saved the packages and
recovered offline changes (observed on synthetic samples, one to three changed
1K files end to end in 3.0–4.0 s, event throttle 500 ms, idle wait 1.0 s,
threshold 1.0 s). A change of all twelve files, 2K output and production scenes
were not measured; twelve files alone take 2.8 s in UE, so the five-second
target is not shown for them. In an idle-machine retest a background reimport
never took the foreground from Painter (UE visible behind it or minimized, and
one end-to-end run); tablet painting is checked in M1. No UE component is built for M1 (UE-02 and POC-06 are not
needed).

Required UE configuration, documented for users in M1 (UE-01):

- Monitor Content Directories on; the source-image directory listed by absolute
  path with a mount point (UE refuses an unmapped absolute directory); include
  wildcard `*.png`.
- Auto Create Assets, Auto Delete Assets and Prompt Before Action off.
- Detect Changes On Startup on, so changes made while UE is closed are imported.

Starting parameters: `TextureStateEvent` throttle 500 ms while real-time sync is
active (global, restored on close), 1.0 s idle wait, UE Import Threshold 1.0 s
(0.3 s was also measured, three files in 0.97 s; lower values are untested), 1K
output by default. Background CPU throttling did not change latency in M0 and
needs no setting.

Consequences: the panel reports "file published", never "UE complete"; source
association checks in UE stay with the user; a full sync is the recovery path
when UE missed changes. Production scenes are not yet verified; a blocker found
there reopens this decision rather than being worked around silently.
