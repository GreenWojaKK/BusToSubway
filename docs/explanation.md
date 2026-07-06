# Explanation: Why BusToSubway Is Designed This Way

This document explains the design rationale. For schemas and definitions, see [Reference](reference.md).

## 1. The Name Is About Representation

BusToSubway is not a modal-transfer project. It is not about finding routes from buses to subways.

The name points to a data asymmetry. Subway data normally has station boundaries, line paths, and transfer stations already encoded. Bus data often has only stop poles and route stop order. What is observed in subway data must be inferred in bus data.

The project asks: can we build those missing representation layers from the network structure itself?

## 2. Standards Give Vocabulary, Not the Decision Rule

IFOPT and NeTEx already distinguish Quay and StopPlace. National stop registries such as NaPTAN or Entur maintain those layers in practice. But the grouping is usually curated: a registry says which Quays belong to which StopPlace.

Many bus datasets do not have that registry layer. They contain Quays but not StopPlaces.

BusToSubway borrows the vocabulary from the standards, but replaces the missing declaration with a reproducible procedure. That is the key design choice: use standard concepts, but make the judgment computable and auditable.

## 3. Why L-space

Transit networks can be represented in several ways.

P-space connects stops that share a route. It is useful for "can I ride without transferring?" but it quickly becomes dense and hides local geometry.

L-space connects only consecutive stops on a route. It preserves local branching. For hub discovery, that matters: a transfer-capable place is not just a place served by many routes, but a place where routes split, cross, or recombine.

That is why BusToSubway starts from L-space.

## 4. Corridor Stop and Interchange Are Different

A corridor stop can look important because many routes share the same road. But if they all enter and leave in the same direction, the stop is mostly a through-point.

An interchange-capable stop has a different structure. Routes approach and leave through multiple arms. The place is not merely on a busy corridor; it is a structural junction.

Simple degree helps, but it is not enough. Degree sees local branching, yet it can still overrate local quirks. We need a way to ask whether local dominance survives at a larger scale.

## 5. Lifetime as Survival Radius

The lifetime idea is simple:

1. Give each node a dominance score, usually L-space degree.
2. For a node `v`, look at its 1-hop neighborhood.
3. Ask whether `v` is still dominant inside that neighborhood.
4. Expand to 2 hops, then 3 hops, and so on.
5. The largest radius where `v` remains dominant is its lifetime.

This measures the radius of local leadership. A small branch point may dominate its immediate surroundings but lose quickly to a nearby terminal. A major hub remains dominant over a wider neighborhood.

The idea is "persistence-like" in the informal sense that a property is tracked across scale. It is not persistent homology. There is no homology class and no topological filtration in the strict TDA sense. The shared intuition is survival across scale.

## 6. Why an Explorer, Not Only a Ranking

There is no single final answer to "what is a bus hub?" The answer depends on scale, dominance score, tie handling, and whether we care about neighborhood hubs or regional hubs.

So the project should not hide parameters behind one CSV. It should expose them:

- degree versus lifetime,
- ego radius `k`,
- grouping choices,
- manual review candidates,
- official hub overlays.

The visual explorer is part of the method. It lets the reviewer see corridor stops fall away as radius grows, and see which places remain structurally dominant.

## 7. What the Method Does Not Claim

BusToSubway does not estimate actual passenger transfer behavior. It does not use fare-card trip chains or observed alighting-to-boarding transfers.

That is intentional. The project builds a structural representation. Passenger behavior data can validate, challenge, or enrich it later.

Useful disagreements include:

- structurally strong hub with little observed transfer behavior;
- official transfer center with weak structural score;
- high-ridership place that is important as a destination but not as a network junction.

Those mismatches are not failures. They are the point of separating structure from behavior.

## 8. Limits

The method has clear limits:

- Lifetime depends on the dominance score. If the score misses a kind of importance, lifetime will miss it too.
- StopPlace grouping is sensitive to street geometry, naming conventions, and data quality.
- Static topology ignores headways, service span, and time-of-day variation unless those are added as separate layers.
- Tie handling matters in grid-like networks.

These limits should be surfaced in the outputs and UI rather than hidden.

## 9. What This Enables

Once bus data has StopPlaces and structural hub candidates, new questions become practical:

- How does a redesign change the hub hierarchy?
- Which official hubs are structurally supported by the network?
- Which undesignated places behave like latent transfer hubs?
- Can Quay-only public data be exported toward a StopPlace-aware standard?
- Which corridor stops are busy but not structurally interchangeable?

BusToSubway is a representation layer for those questions.
