# BusToSubway

> Giving bus data the representation that subway data gets for free.

BusToSubway turns raw bus stop and route-sequence data into higher-level transit representations: logical stations, route adjacency, and transfer-hub candidates. It is not about transferring from bus to subway. The name means: bus data should be given the same kind of structural layer that subway data already has.

## The Problem

Subway data usually arrives with three things already decided:

1. what counts as one station,
2. which path each line follows,
3. which stations work as transfer hubs.

Bus data usually does not. It often gives individual stops, coordinates, and route-by-route stop order. In IFOPT terms, the data is mostly at the **Quay** level, while the **StopPlace** layer is missing.

That creates two common distortions:

- Several individual stops that function as one place appear as unrelated points.
- Busy corridor stops look important simply because many routes pass through them, even when they do not function as real interchange points.

BusToSubway treats these missing layers as an inference problem.

## What It Builds

The pipeline builds a representation layer over bus data:

```text
Raw bus data
  stops + route stop sequences
        |
        v
StopPlace grouping
  split individual stops -> logical places
        |
        v
L-space graph
  nodes = StopPlaces, edges = consecutive stops on a route
        |
        v
Hub metrics
  degree, physical arms, lifetime / survival radius
        |
        v
Reviewable outputs
  maps, tables, hub candidates, diagnostics
```

The core idea is not just to count routes. A corridor stop can have high degree because many routes share the same road. A real interchange has a different structure: routes split, cross, and reconnect there.

## The Main Idea: Lifetime

For a stop or StopPlace `v`, start with a dominance score such as L-space degree. Then expand the `k`-hop neighborhood around `v`.

`lifetime(v)` is the largest radius `k` where `v` remains at least as dominant as every other node in that neighborhood.

In plain terms:

- A local stop may be strongest in its immediate neighborhood, then lose to a larger hub two hops away.
- A structural hub remains dominant even as the search radius expands.
- Lifetime turns "is this stop large?" into "how far does this stop's influence persist?"

This separates many corridor stops from interchange-capable places without claiming that there is one universal definition of a hub.

## Why This Matters

This representation makes several downstream questions possible:

- Which bus stops should be treated as one logical station?
- Which places are structurally plausible transfer hubs?
- How does a network redesign change the hub hierarchy?
- Where do official transfer-center designations agree or disagree with the network structure?
- Can Quay-only bus data be exported toward a StopPlace-aware standard model?

The project does not infer actual passenger transfer behavior. Fare-card or AFC data would be a validation layer, not a prerequisite for the structural representation built here.

## Current Status

This repository is being refactored from a research/prototype pipeline into a public portfolio project.

- Implemented engineering pipeline: ingestion, canonical route construction, StopPlace construction, and hub discovery for the current data track.
- Designed but still being packaged for public use: generalized CLI/API, sample data walkthrough, and interactive explorer.
- Internal design, audit, and verification notes live outside this public-facing document set. They are useful for engineering continuity, but they are not the best entry point for a GitHub reader.

## Documentation

| Document | Use it when |
|---|---|
| [Tutorial](tutorial.md) | You want a first end-to-end walkthrough |
| [How-to Guides](how-to.md) | You want to adapt the method to your own data |
| [Reference](reference.md) | You need schemas, parameters, and formal definitions |
| [Explanation](explanation.md) | You want the design rationale and conceptual background |

For a quick review, read this page first, then [Explanation §4-6](explanation.md#4-corridor-stop-and-interchange-are-different).
