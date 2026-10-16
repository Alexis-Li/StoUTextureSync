# Export through Painter and update existing UE assets

StoUTextureSync orchestrates Painter's official export API and the selected
export preset, then publishes complete local images for existing UE Textures.
First import and material wiring remain user-owned. This preserves the current
asset workflow and export quality while avoiding material reconstruction and
a separate pixel-generation implementation.

Route A uses UE Auto Reimport and is the initial prototype direction. It keeps
installation small but can report only file publication, not UE completion.
If configured background refresh, recovery or required receipt semantics remain
insufficient, route B adds a minimal UE execution component for directed reimport
and correlated results. Such a component belongs in this product repository;
SP and UE do not become separate Git repositories.

Single-file completeness is required. Files may become visible one at a time;
there is no cross-file atomicity promise. Partial failures pause automatic work
and recover by retrying valid staged outputs or by a new full sync. These choices
avoid requiring a custom UE component solely to hide short mixed transitions.

Painter UV Tiles are a drawing organization mechanism for body parts. Outputs
are mapped to ordinary source-image filenames and independent UE Textures.
Clothing and body naming modes are configured per Texture Set.

M0 owns the evidence for export filters, identity, publication mechanism,
background reimport and the five-second target. Online API documentation and
reference plug-ins do not establish compatibility with Painter 11.0 or either
target UE build. See [M0](https://github.com/Alexis-Li/StoUTextureSync/issues/2).
