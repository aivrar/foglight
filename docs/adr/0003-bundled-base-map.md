# ADR 0003: Bundled Base Map

Status: accepted and implemented

## Context

The former CARTO raster layer was network-dependent and was not a universally
licensed zero-cost default. OpenStreetMap's public tile service also forbids
bulk/offline use and is not an application CDN.

## Decision

Bundle a simplified Natural Earth 1:110m world dataset and render it locally as
the always-available base. Vendor Leaflet in the artifact. Hosted tiles may be
an explicit optional enhancement only after provider terms, attribution,
traffic, privacy, and commercial-use review. Foglight never prefetches hosted
tiles for offline use.

## Consequences

The core map works without internet tiles or keys. The offline base is less
detailed, so optional hosted maps remain useful but can never block core use.
