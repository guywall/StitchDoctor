"""Reversible fix operations.

Each op is a pure function: (pattern, params) -> new pattern. The API layer
persists the result as the next version, so undo = drop the version.
"""
from __future__ import annotations

import math
from typing import Callable, Dict, Optional

import pyembroidery
from pyembroidery import (
    COLOR_CHANGE,
    COMMAND_MASK,
    END,
    JUMP,
    NEEDLE_SET,
    SEQUIN_EJECT,
    SEQUIN_MODE,
    STOP,
    STITCH,
    TRIM,
    EmbPattern,
)

from .. import settings

OpsMap = Dict[str, Callable[..., EmbPattern]]


def _clone(pattern: EmbPattern) -> EmbPattern:
    """Deep-copy via pyembroidery's lossless JSON round-trip."""
    import os
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        pyembroidery.write_json(pattern, path)
        return pyembroidery.read_json(path)
    finally:
        os.unlink(path)


def _copy_threads(src: EmbPattern, dst: EmbPattern) -> None:
    for thread in src.threadlist:
        dst.threadlist.append(thread)


_MOVE_COMMANDS = {JUMP, TRIM, COLOR_CHANGE, NEEDLE_SET, STOP,
                  SEQUIN_MODE, SEQUIN_EJECT}


def _iterate_blocks(pattern: EmbPattern):
    """Yield (list-of-stitches, command) blocks split at non-STITCH commands.

    Each yielded block is a list of [x, y, cmd] with cmd == STITCH; the
    separator command that ended the block is yielded afterwards.
    """
    block = []
    pos = None
    for s in pattern.stitches:
        cmd = s[2] & COMMAND_MASK
        if cmd == END:
            break
        if cmd == STITCH:
            block.append([s[0], s[1], STITCH])
            pos = (s[0], s[1])
        else:
            if block:
                yield block
                block = []
            yield [s[0], s[1], cmd]
            pos = (s[0], s[1])
    if block:
        yield block


def remove_micro_stitches(pattern: EmbPattern,
                          threshold_mm: Optional[float] = None) -> EmbPattern:
    """Drop stitches shorter than threshold, merging endpoints.

    stitch i-1 -> i -> i+1 becomes i-1 -> i+1 (i removed entirely).
    """
    t = (threshold_mm if threshold_mm is not None
         else settings.MICRO_STITCH_MM) * settings.UNITS_PER_MM
    out = EmbPattern()
    kept_prev = None  # last accepted absolute position
    removed = 0

    for s in pattern.stitches:
        cmd = s[2] & COMMAND_MASK
        if cmd == END:
            break
        if cmd == STITCH:
            pt = (s[0], s[1])
            if kept_prev is None:
                kept_prev = pt
                out.add_stitch_absolute(STITCH, pt[0], pt[1])
                continue
            d = math.hypot(pt[0] - kept_prev[0], pt[1] - kept_prev[1])
            if d < t:
                removed += 1
                # skip this stitch; the next accepted stitch connects
                # directly to kept_prev
                continue
            out.add_stitch_absolute(STITCH, pt[0], pt[1])
            kept_prev = pt
        else:
            out.add_stitch_absolute(cmd, s[0], s[1])
            kept_prev = None if cmd in _MOVE_COMMANDS else kept_prev
    _copy_threads(pattern, out)
    if removed == 0:
        raise ValueError("no micro-stitches found to remove")
    return out


def add_trims(pattern: EmbPattern, min_jump_mm: Optional[float] = None) -> EmbPattern:
    """Insert a TRIM before every jump longer than min_jump_mm that does not
    already follow a trim."""
    t = (min_jump_mm if min_jump_mm is not None
         else settings.LONG_JUMP_MM) * settings.UNITS_PER_MM
    out = EmbPattern()
    added = 0
    last_cmd = None
    pos = None
    for s in pattern.stitches:
        cmd = s[2] & COMMAND_MASK
        if cmd == END:
            break
        if cmd == JUMP and last_cmd != TRIM:
            length = math.hypot(s[0], s[1]) if pos is None else \
                math.hypot(s[0] - pos[0], s[1] - pos[1])
            if length > t:
                out.add_command(TRIM)
                added += 1
        out.add_stitch_absolute(cmd, s[0], s[1])
        last_cmd = cmd
        if cmd == STITCH or cmd == JUMP or s[0] or s[1]:
            pos = (s[0], s[1])
    _copy_threads(pattern, out)
    if added == 0:
        raise ValueError("no jumps found needing trims")
    return out


def split_long_stitches(pattern: EmbPattern,
                        max_mm: Optional[float] = None) -> EmbPattern:
    """Insert intermediate points so no stitch exceeds max_mm."""
    t = (max_mm if max_mm is not None else settings.LONG_STITCH_MM) \
        * settings.UNITS_PER_MM
    out = EmbPattern()
    pos = None
    split = 0
    for s in pattern.stitches:
        cmd = s[2] & COMMAND_MASK
        if cmd == END:
            break
        if cmd == STITCH:
            pt = (s[0], s[1])
            if pos is None:
                pos = pt
            dx = pt[0] - pos[0]
            dy = pt[1] - pos[1]
            dist = math.hypot(dx, dy)
            if dist > t:
                steps = int(math.ceil(dist / t))
                for k in range(1, steps):
                    out.add_stitch_absolute(
                        STITCH,
                        pos[0] + dx * k / steps,
                        pos[1] + dy * k / steps,
                    )
                split += 1
            out.add_stitch_absolute(STITCH, pt[0], pt[1])
            pos = pt
        else:
            out.add_stitch_absolute(cmd, s[0], s[1])
            if s[0] != 0 or s[1] != 0:  # positionless cmds are stored [0,0,cmd]
                pos = (s[0], s[1])
    _copy_threads(pattern, out)
    if split == 0:
        raise ValueError("no stitches found needing splits")
    return out


def remove_isolated_stitches(pattern: EmbPattern,
                             min_distance_mm: Optional[float] = None) -> EmbPattern:
    """Remove single-stitch runs whose nearest neighbour run is far away."""
    d_mm = (min_distance_mm if min_distance_mm is not None
            else settings.ISOLATED_RUN_MM)
    runs = []  # each: (indices, points)
    current = []
    for idx, s in enumerate(pattern.stitches):
        cmd = s[2] & COMMAND_MASK
        if cmd == END:
            break
        if cmd == STITCH:
            current.append(idx)
        else:
            if current:
                runs.append(current)
                current = []
    if current:
        runs.append(current)

    centres = []
    for indices in runs:
        pts = [(pattern.stitches[i][0], pattern.stitches[i][1])
               for i in indices]
        centres.append((sum(p[0] for p in pts) / len(pts),
                        sum(p[1] for p in pts) / len(pts)))

    removed = set()
    for i, indices in enumerate(runs):
        if len(indices) != 1:
            continue
        cx, cy = centres[i]
        nearest = None
        for j, other in enumerate(runs):
            if j == i:
                continue
            d = math.hypot(cx - centres[j][0], cy - centres[j][1])
            if nearest is None or d < nearest:
                nearest = d
        if nearest is not None and nearest / settings.UNITS_PER_MM > d_mm:
            removed.add(indices[0])

    if not removed:
        raise ValueError("no isolated stitches found to remove")

    out = EmbPattern()
    for idx, s in enumerate(pattern.stitches):
        cmd = s[2] & COMMAND_MASK
        if cmd == END:
            break
        if idx in removed:
            continue
        out.add_stitch_absolute(cmd, s[0], s[1])
    _copy_threads(pattern, out)
    return out


def _blocks_with_separators(pattern: EmbPattern):
    """Split the pattern into colour blocks + the separators between them.

    Returns (blocks, separators) where blocks[i] is a list of [x, y, cmd]
    records (stitches, jumps, trims, stops — anything between two colour
    changes) and separators[i] is the COLOR_CHANGE/NEEDLE_SET record that
    ends block i. len(separators) == len(blocks) - 1.
    """
    blocks = []
    separators = []
    current = []
    for s in pattern.stitches:
        cmd = s[2] & COMMAND_MASK
        if cmd == END:
            break
        if cmd in (COLOR_CHANGE, NEEDLE_SET):
            blocks.append(current)
            current = []
            separators.append([s[0], s[1], cmd])
        else:
            current.append([s[0], s[1], cmd])
    blocks.append(current)
    # a trailing colour change with no stitches after it is not a block
    if blocks and not blocks[-1] and separators:
        blocks.pop()
        separators.pop()
    return blocks, separators


def _assemble(blocks, separators, threads_src=None) -> EmbPattern:
    """Rebuild a pattern from blocks + separators, preserving threadlist.

    Travel guard: when a block now starts far from where the needle sits
    (block moved/reversed), insert a JUMP to the block's first stitch so the
    connection is needle-up travel rather than a stray long stitch.
    """
    out = EmbPattern()
    guard = settings.LONG_JUMP_MM * settings.UNITS_PER_MM
    pos = None
    for i, block in enumerate(blocks):
        if i > 0:
            sep = separators[i - 1]
            out.add_stitch_absolute(sep[2], sep[0], sep[1])
        for j, s in enumerate(block):
            cmd = s[2] & COMMAND_MASK
            if (j == 0 and cmd == STITCH and pos is not None
                    and math.hypot(s[0] - pos[0],
                                   s[1] - pos[1]) > guard):
                out.add_stitch_absolute(JUMP, s[0], s[1])
            out.add_stitch_absolute(cmd, s[0], s[1])
            if cmd != TRIM and cmd != COLOR_CHANGE:
                pos = (s[0], s[1]) if (s[0] or s[1]) else pos
    out.add_command(END)
    if threads_src is not None:
        _copy_threads(threads_src, out)
    return out


def _block_entry_exit(block) -> tuple:
    """(entry, exit) positions of a block: first and last positioned record."""
    entry = exit_ = None
    for s in block:
        if s[0] or s[1] or (s[2] & COMMAND_MASK) in (STITCH, JUMP):
            if entry is None:
                entry = (s[0], s[1])
            exit_ = (s[0], s[1])
    return entry, exit_


def _travel_optimised_order(blocks: list) -> list:
    """Greedy nearest-neighbour block order minimising needle-up travel.

    At each step we stand at the previous block's EXIT and pick the block
    whose ENTRY is closest. Only reorders when the greedy tour actually
    beats the original — a tiny saving is not worth a colour-order change.
    """
    scale = settings.UNITS_PER_MM
    remaining = list(range(len(blocks)))

    def centre(block) -> tuple:
        pts = [(s[0], s[1]) for s in block if s[0] or s[1]]
        if not pts:
            return (0.0, 0.0)
        return (sum(p[0] for p in pts) / len(pts) / scale,
                sum(p[1] for p in pts) / len(pts) / scale)

    def entry_mm(block) -> tuple:
        e, _ = _block_entry_exit(block)
        if e is None:
            return centre(block)
        return (e[0] / scale, e[1] / scale)

    def exit_mm(block) -> tuple:
        _, x = _block_entry_exit(block)
        if x is None:
            return centre(block)
        return (x[0] / scale, x[1] / scale)

    def tour_cost(order, entries, exits) -> float:
        pos = (0.0, 0.0)  # needle starts at the origin
        cost = 0.0
        for bi in order:
            e, x = entries[bi], exits[bi]
            cost += math.dist(pos, e)
            pos = x
        return cost

    entries = {i: entry_mm(b) for i, b in enumerate(blocks)}
    exits = {i: exit_mm(b) for i, b in enumerate(blocks)}

    order = []
    pos = (0.0, 0.0)
    while remaining:
        best = min(remaining, key=lambda i: math.dist(pos, entries[i]))
        order.append(best)
        remaining.remove(best)
        pos = exits[best]

    # keep the original order when optimisation does not meaningfully help
    original = list(range(len(blocks)))
    if tour_cost(original, entries, exits) <= tour_cost(order, entries, exits) + 0.5:
        return original
    return order


def reorder_blocks(pattern: EmbPattern, order: Optional[list] = None) -> EmbPattern:
    """Reorder colour blocks (0-based, new order as a permutation).

    Block k = everything from a color change/needle set to the next one.
    With order=None, optimise automatically: greedy nearest-neighbour from
    the needle's current position, re-evaluated from each block's *exit*
    point (its last stitch), which is what the next jump actually leaves
    from. Colour changes stay expensive (machine pauses), so a move is only
    taken when it saves real travel.
    """
    blocks, separators = _blocks_with_separators(pattern)
    if not blocks:
        raise ValueError("pattern has no blocks to reorder")
    if order is None:
        order = _travel_optimised_order(blocks)
        if order == list(range(len(blocks))):
            raise ValueError(
                "reordering would not reduce travel for this design — "
                "the current order is already the shortest greedy tour")
    if sorted(order) != list(range(len(blocks))):
        raise ValueError(
            f"order must be a permutation of 0..{len(blocks) - 1}")

    out = EmbPattern()
    for pos_in_order, block_index in enumerate(order):
        if pos_in_order > 0:
            out.add_command(COLOR_CHANGE)
        for s in blocks[block_index]:
            out.add_stitch_absolute(s[2], s[0], s[1])
    out.add_command(END)
    _copy_threads(pattern, out)
    # reorder threadlist to match
    new_threads = []
    for block_index in order:
        if block_index < len(pattern.threadlist):
            new_threads.append(pattern.threadlist[block_index])
    out.threadlist = new_threads
    return out


def _validate_block_index(blocks, index, action: str) -> int:
    if not blocks:
        raise ValueError("pattern has no colour blocks")
    try:
        index = int(index)
    except (TypeError, ValueError):
        raise ValueError("block index must be an integer")
    if not 0 <= index < len(blocks):
        raise ValueError(
            f"cannot {action} block {index}: pattern has {len(blocks)} blocks")
    return index


def move_block(pattern: EmbPattern, index: int = 0, to: int = 0) -> EmbPattern:
    """Move one colour block to a new position in the sew order.

    The threadlist follows the block, so previews and exports keep the
    right colours per block.
    """
    blocks, separators = _blocks_with_separators(pattern)
    index = _validate_block_index(blocks, index, "move")
    n = len(blocks)
    try:
        to = int(to)
    except (TypeError, ValueError):
        raise ValueError("target position 'to' must be an integer")
    if not 0 <= to < n:
        raise ValueError(f"target position {to} out of range (0..{n - 1})")
    if to == index:
        return pattern
    block = blocks.pop(index)
    blocks.insert(to, block)
    out = _assemble(blocks, separators, threads_src=pattern)
    out.threadlist = [
        pattern.threadlist[i] for i in _origin_indices(len(blocks), index, to)
        if i < len(pattern.threadlist)]
    return out


def _origin_indices(n: int, src: int, dst: int) -> list:
    """Original block indices for the order after moving src to dst."""
    order = list(range(n + 1))  # before the pop
    order.pop(src)
    order.insert(dst, src)
    return order


def reverse_block(pattern: EmbPattern, index: int = 0) -> EmbPattern:
    """Reverse the stitch direction within one colour block.

    Classic digitising fix: sew a fill/satin the other way so the last
    stitch tucks the previous one, or to shorten the jump out of the block.
    Stitches, jumps and trims inside the block all reverse together.
    """
    blocks, separators = _blocks_with_separators(pattern)
    index = _validate_block_index(blocks, index, "reverse")
    blocks[index] = list(reversed(blocks[index]))
    return _assemble(blocks, separators, threads_src=pattern)


def delete_block(pattern: EmbPattern, index: int = 0) -> EmbPattern:
    """Delete a colour block entirely (e.g. a stray artefact run).

    The block's separator goes with it; the threadlist entry is dropped so
    colours stay aligned with blocks.
    """
    blocks, separators = _blocks_with_separators(pattern)
    index = _validate_block_index(blocks, index, "delete")
    blocks.pop(index)
    if separators and index - 1 < len(separators):
        separators.pop(index - 1 if index > 0 else 0)
    if not blocks:
        raise ValueError("cannot delete the only colour block")
    out = _assemble(blocks, separators, threads_src=pattern)
    out.threadlist = [t for i, t in enumerate(pattern.threadlist)
                      if i != index and i < len(pattern.threadlist)]
    return out


def merge_blocks(pattern: EmbPattern, index: int = 0, with_index: int = 1) -> EmbPattern:
    """Merge two adjacent colour blocks into one (keeps the first's colour).

    Useful when an auto-digitiser split one logical shape across two blocks
    with the same or nearly-identical thread.
    """
    blocks, separators = _blocks_with_separators(pattern)
    index = _validate_block_index(blocks, index, "merge")
    try:
        with_index = int(with_index)
    except (TypeError, ValueError):
        raise ValueError("'with_index' must be an integer")
    if not 0 <= with_index < len(blocks):
        raise ValueError(
            f"cannot merge with block {with_index}: pattern has {len(blocks)} blocks")
    if abs(with_index - index) != 1:
        raise ValueError("merge_blocks requires adjacent block indices")
    lo, hi = sorted((index, with_index))
    blocks[lo] = blocks[lo] + blocks[hi]
    blocks.pop(hi)
    if hi - 1 < len(separators):
        separators.pop(hi - 1)
    out = _assemble(blocks, separators, threads_src=pattern)
    out.threadlist = [t for i, t in enumerate(pattern.threadlist)
                      if i != hi and i < len(pattern.threadlist)]
    return out


OPS: OpsMap = {
    "remove_micro_stitches": remove_micro_stitches,
    "add_trims": add_trims,
    "split_long_stitches": split_long_stitches,
    "remove_isolated_stitches": remove_isolated_stitches,
    "reorder_blocks": reorder_blocks,
    "move_block": move_block,
    "reverse_block": reverse_block,
    "delete_block": delete_block,
    "merge_blocks": merge_blocks,
}
