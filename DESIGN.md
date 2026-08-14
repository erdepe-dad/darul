---
name: Darul Graph Atlas
description: A living washi graph sheet that folds structural code knowledge toward a question.
colors:
  vermillion: "#d83c2e"
  vermillion-deep: "#b72a20"
  vermillion-wash: "#e47a6e"
  fold-white: "#f7f3ee"
  paper-gray: "#e6e2da"
  paper-dim: "#d2cec6"
  sumi: "#1a1a18"
  sumi-soft: "#5f5a54"
  focus-gold: "#d4af37"
  structural-blue: "#315f89"
  structural-green: "#4c725d"
typography:
  headline:
    fontFamily: "Commissioner, Helvetica Neue, sans-serif"
    fontSize: "24px"
    fontWeight: 500
    lineHeight: 1.18
    letterSpacing: "-0.025em"
  title:
    fontFamily: "Commissioner, Helvetica Neue, sans-serif"
    fontSize: "17px"
    fontWeight: 500
    lineHeight: 1.25
    letterSpacing: "-0.015em"
  body:
    fontFamily: "Commissioner, Helvetica Neue, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.6
  label:
    fontFamily: "Commissioner, Helvetica Neue, sans-serif"
    fontSize: "10px"
    fontWeight: 650
    lineHeight: 1.2
    letterSpacing: "0.13em"
rounded:
  paper: "2px"
  circular: "999px"
spacing:
  crease: "14px"
  control: "8px"
  panel: "22px"
  touch: "44px"
components:
  button-primary:
    backgroundColor: "{colors.vermillion}"
    textColor: "{colors.fold-white}"
    typography: "{typography.label}"
    rounded: "{rounded.paper}"
    padding: "0 13px"
    height: "42px"
  input:
    backgroundColor: "{colors.fold-white}"
    textColor: "{colors.sumi}"
    typography: "{typography.body}"
    rounded: "{rounded.paper}"
    padding: "0 12px"
    height: "42px"
---

# Design System: Darul Graph Atlas

## Overview

**Creative North Star: "The Folded Neighborhood"**

Darul treats the knowledge graph as one living sheet, not a cloud of database chrome. Vermillion washi surrounds a fold-white working surface; crisp crease geometry organizes code, routes, sessions, and decisions into a legible engineering atlas.

The interface is operational and dense, but it stays tactile. A question narrows the sheet, a selected form gains a gold registration mark, and neighborhood navigation performs one authored fold while preserving spatial memory.

**Key Characteristics:**

- One dominant graph sheet framed by narrow scope and evidence margins.
- Vermillion material, sumi typography, gold focus, and restrained structural colors.
- Sharp paper corners, hairline creases, compact uppercase labels, and explicit direction.
- Responsive drawers that preserve the graph as the primary mobile surface.

## Colors

The palette reads as printed ink on warm paper: vermillion establishes the world, while structural colors classify graph forms without competing with selection.

### Primary

- **Washi Vermillion:** The surrounding field, primary actions, checked filters, and route emphasis.
- **Deep Vermillion:** Hover and pressed reinforcement where the primary ink needs more weight.

### Secondary

- **Registration Gold:** Selected nodes, active history markers, range thumbs, and keyboard focus.
- **Structural Blue and Green:** Code-file and class distinctions inside the graph.

### Neutral

- **Fold White:** The graph sheet, drawers, dialogs, and high-contrast text against vermillion.
- **Sumi:** Primary text and function nodes.
- **Soft Sumi:** Secondary explanations, metadata, counts, and inactive history.
- **Paper Gray and Paper Dim:** Keyboard keys, dividers, field strokes, and quiet boundaries.

**The Gold Means Focus Rule.** Gold is reserved for current selection, active lineage, and focus; it is never general decoration.

## Typography

**Display Font:** Commissioner (with Helvetica Neue fallback)
**Body Font:** Commissioner (with Helvetica Neue fallback)

**Character:** Commissioner carries both the atlas labels and explanatory prose. Its variable weight range supports technical density without turning the product into monospace-themed tooling.

### Hierarchy

- **Headline:** Medium, compact headings for inspector and empty states.
- **Title:** Calm panel propositions and node titles with tight tracking.
- **Body:** Readable operational copy with generous line height.
- **Label:** Semibold uppercase text with wide tracking for controls, filters, and graph taxonomy.

**The Atlas Label Rule.** Uppercase tracking belongs to navigation and taxonomy; sentences and graph names remain natural case.

## Layout

Desktop uses a three-part atlas: a 278px scope rail, a flexible graph sheet, and a 328px inspector separated by 14px creases. The graph owns the majority of the viewport and fits within the available height.

Below 860px, the sheet becomes the full composition. Scope and inspector become off-canvas drawers, the toolbar stays visible above the canvas, controls use 44px touch targets, and the shell uses dynamic viewport height to avoid a mobile dead zone.

## Elevation & Depth

Depth is structural rather than decorative. The main paper sheet receives one wide ambient shadow; drawers and dialogs use broader shadows only while lifted above the sheet. Most hierarchy comes from material contrast, dividers, clipping, and crease geometry.

**The Paper Stack Rule.** Shadows appear only when one physical sheet sits above another; ordinary controls remain flat.

## Shapes

Surfaces use nearly square 2px paper corners. Graph entities carry taxonomy-specific silhouettes—folds, circles, triangles, diamonds, routes, and decision stars—while circular marks are reserved for focus, history, and continuous forms. Diagonal geometry should feel measured and crisp, never sketched.

## Components

### Buttons

- **Shape:** Sharp paper corners with compact horizontal padding.
- **Primary:** Vermillion fill, fold-white uppercase label, and a soft downward shadow.
- **Hover / Focus:** Deepen the vermillion on hover; use a 3px gold focus outline with visible offset.
- **Toolbar:** Transparent fold-white actions; mobile icon controls keep a 44px target.

### Inputs / Fields

- **Style:** Warm translucent white over paper, 1px paper-dim stroke, and 2px corners.
- **Focus:** Vermillion stroke plus a soft downward ambient shadow.
- **Labels:** Small uppercase atlas labels above each field.

### Navigation

- **Style:** Exploration history is a numbered vertical lineage with a small registration marker.
- **State:** The current fold uses gold; previous and forward steps remain visible so navigation never destroys the path.
- **Mobile:** Scope and detail move into accessible drawers while Folded/Free and graph tools stay on the main sheet.

### Folded Graph Sheet

The signature component combines canvas topology, subtle paper fibers, diagonal crease lines, directional arrowheads, persistent labels for high-value forms, and relationship labels on selected edges. Folding briefly clips and compresses the sheet before the two-hop neighborhood settles into preserved positions.

## Do's and Don'ts

### Do:

- **Do** keep the graph as the dominant surface at every viewport.
- **Do** persist labels for repositories, selection, search matches, and structural hubs.
- **Do** preserve navigation history and spatial memory through folds.
- **Do** use gold only for focus and current state.

### Don't:

- **Don't** replace the paper atlas with generic floating dashboard cards.
- **Don't** hide essential graph controls on mobile.
- **Don't** use color alone to identify node taxonomy or direction.
- **Don't** scatter unrelated micro-animations; the fold is the authored motion moment.
