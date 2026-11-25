"""Constrain a Regular-to-Bold Bézier interpolation within a single contour."""
from functools import cached_property


def dot(a, b):
    return (a.conjugate() * b).real


def halves(points):
    left = [points[0]]
    right = [points[-1]]
    while len(points) > 1:
        points = tuple((a + b) / 2 for a, b in zip(points, points[1:]))
        left.append(points[0])
        right.append(points[-1])
    return tuple(left), tuple(reversed(right))


def bounds(points):
    x = [p.real for p in points]
    y = [p.imag for p in points]
    return (min(x), max(x)), (min(y), max(y))


class Span:
    """Corresponding pieces of the two masters. Subdivision commutes with interpolation."""
    def __init__(self, regular, bold):
        self.regular = regular
        self.bold = bold
        self.rbox = bounds(regular)
        self.bbox = self.rbox if bold is regular else bounds(bold)
        self.size = max(hi - lo for box in (self.rbox, self.bbox) for lo, hi in box)

    @cached_property
    def children(self):
        source = halves(self.regular)
        target = source if self.bold is self.regular else halves(self.bold)
        return tuple(Span(r, b) for r, b in zip(source, target))


def order(a, b):
    # Touching projected intervals can share a knot. At least one control
    # must lie strictly to one side for the interiors to be disjoint.
    if a[1] <= b[0] and a[0] < b[1]:
        return 1
    if b[1] <= a[0] and b[0] < a[1]:
        return -1
    return 0


def projection(points, axis):
    values = [dot(p, axis) for p in points]
    return min(values), max(values)


def separated(a, b, depth=12):
    """Prove that the interiors stay apart for *all* interpolated weights."""
    for i in (0, 1):
        side = order(a.rbox[i], b.rbox[i])
        if side and side == order(a.bbox[i], b.bbox[i]):
            return True
    for curve in (a.regular, b.regular, a.bold, b.bold):
        axis = 1j * (curve[-1] - curve[0])
        if axis:
            side = order(projection(a.regular, axis), projection(b.regular, axis))
            if side and side == order(projection(a.bold, axis), projection(b.bold, axis)):
                return True
    if not depth or not max(a.size, b.size):
        return False
    if a.size < b.size:
        a, b = b, a
    return all(separated(piece, b, depth - 1) for piece in a.children)


def separated_across_weight(a, b, depth=3):
    if separated(a, b):
        return True
    if not depth or not separated(Span(a.bold, a.bold), Span(b.bold, b.bold)):
        return False
    am = tuple((p+q)/2 for p, q in zip(a.regular, a.bold))
    bm = tuple((p+q)/2 for p, q in zip(b.regular, b.bold))
    return (separated_across_weight(Span(a.regular, am), Span(b.regular, bm), depth-1)
            and separated_across_weight(Span(am, a.bold), Span(bm, b.bold), depth-1))


def forward(steps):
    # The projected derivative is a quadratic in the Bernstein basis. Its
    # middle coefficient may be negative while the derivative stays positive.
    a, b, c = steps
    return min(a, c) >= 0 and (b >= 0 or b*b <= a*c) and a+b+c > 0


def steps(points, axis):
    return tuple(dot(q - p, axis) for p, q in zip(points, points[1:]))


def directions(curve):
    chord = curve[-1] - curve[0]
    yield chord
    if chord.real:
        yield 1 if chord.real > 0 else -1
    if chord.imag:
        yield 1j if chord.imag > 0 else -1j


def simple(span, depth=5):
    for axis in directions(span.regular):
        if forward(steps(span.regular, axis)) and forward(steps(span.bold, axis)):
            return True
    if not depth:
        return False
    first, last = span.children
    return separated(first, last) and simple(first, depth - 1) and simple(last, depth - 1)


def limit_handles(regular, bold):
    if len(regular) != 4 or regular == bold:
        return bold
    scale = 0
    for axis in directions(regular):
        if not forward(steps(regular, axis)):
            continue
        movement = steps(bold, axis)
        if forward(movement):
            return bold
        a, _, c = movement
        span = sum(movement)
        if span > 0 and min(a, c) >= 0 and a+c > 0:
            scale = max(scale, span / (a+c))
    if scale:
        p0, p1, p2, p3 = bold
        return p0, p0 + scale*(p1-p0), p3 + scale*(p2-p3), p3
    # A long turn may need several projections. Preserve authored loops.
    if simple(Span(regular, bold)) or not simple(Span(regular, regular)):
        return bold
    return None


def nearby_pairs(regular, proposed, fallback):
    # Each fitted point lies in the box of the original, proposed and fallback
    # controls, including after shortening a handle toward its own knot.
    boxes = []
    for i, (r, b, f) in enumerate(zip(regular, proposed, fallback)):
        x, y = bounds((*r, *b, *f))
        boxes.append((*x, *y, i))
    active = []
    for left, right, bottom, top, i in sorted(boxes):
        active = [box for box in active if box[0] >= left]
        for _, low, high, j in active:
            if high >= bottom and top >= low:
                yield j, i
        active.append((right, bottom, top, i))


def separate_regions(a, b, apath=(), bpath=(), depth=8):
    """Keep disjoint portions of an adjacent pair on either side of an authored contact."""
    if separated(a, b):
        yield apath, bpath
    elif depth:
        if a.size >= b.size:
            for k, part in enumerate(a.children):
                yield from separate_regions(part, b, (*apath, k), bpath, depth-1)
        else:
            for k, part in enumerate(b.children):
                yield from separate_regions(a, part, apath, (*bpath, k), depth-1)


def subspan(span, path):
    for half in path:
        span = span.children[half]
    return span


def fit_contour(regular, proposed, fallback):
    count = len(regular)
    strengths = [1.0] * count
    source = [Span(s, s) for s in regular]
    pairs = list(nearby_pairs(regular, proposed, fallback))
    regions = {}
    while True:
        reduce_knots = set()
        spans = []
        for i, (r, b) in enumerate(zip(regular, proposed)):
            j = (i + 1) % count
            if strengths[i] != 1 or strengths[j] != 1:
                # The two handles attached to a knot use its local strength.
                middle = len(r) // 2
                scales = (strengths[i],) * middle + (strengths[j],) * middle
                b = tuple(p + (q-p)*s for p, q, s in zip(fallback[i], b, scales))
            fitted = limit_handles(r, b)
            if fitted is None:
                reduce_knots.update((i, j))
                fitted = b
            spans.append(Span(r, fitted))
        for i, j in pairs:
            if (i, j) not in regions:
                if separated_across_weight(spans[i], spans[j]):
                    continue
                if separated(source[i], source[j]):
                    regions[i, j] = [((), ())]
                elif (i+1) % count == j or (j+1) % count == i:
                    # Shared knots and microscopic CFF closing seams may foil
                    # separation at the ends. The curves' interiors still
                    # need protection against gaining another crossing.
                    regions[i, j] = list(separate_regions(source[i], source[j]))
                else:
                    regions[i, j] = []
            if any(not separated_across_weight(subspan(spans[i], a), subspan(spans[j], b))
                   for a, b in regions[i, j]):
                reduce_knots.update((i, (i+1) % count, j, (j+1) % count))
        if not reduce_knots:
            return [span.bold for span in spans]
        if not any(strengths[i] for i in reduce_knots):
            return None
        for i in reduce_knots:
            strengths[i] = strengths[i] / 2 if strengths[i] > 1/64 else 0


def is_join_cap(first, last):
    """Recognize the two cubic segments of a DigitalKhatt joining end."""
    if len(first) != 4 or len(last) != 4:
        return False
    # Both inner handles coincide at each edge height. The common knot
    # bisects the two inner handle positions. CFF2 rounds to 16.16.
    tolerance = abs(last[-1].imag - first[0].imag) * 1e-5
    return (tolerance > 0 and abs(first[1]-first[2]) <= tolerance
            and abs(last[1]-last[2]) <= tolerance
            and abs((first[1]-first[0]).imag) <= tolerance
            and abs((last[2]-last[3]).imag) <= tolerance
            and abs(first[-1] - (first[2]+last[1])/2) <= tolerance
            and (first[1]-first[0]).real * (last[3]-last[2]).real < 0)


def constrain_contour(regular, proposed, joins=()):
    """Constrain the morph locally and keep the two edges of cursive joins aligned."""
    fitted = fit_contour(regular, proposed, regular)
    if all(abs(fitted[i][0] - proposed[i][0]) < 1e-8 for pair in joins for i in pair):
        return fitted
    # When a tight bend is beside a connector, let its nearby wall move
    # with the connector. Both edges then retain the target join thickness.
    frames = []
    for curve in regular:
        knot = curve[0]
        group = min(joins, key=lambda g: min(abs(knot - regular[k][0]) for k in g))
        first, last = group[0], group[-1]
        a, b = regular[first][0], regular[last][0]
        shift = proposed[first][0] - a
        end_shift = proposed[last][0] - b
        edge = b - a
        slope = (end_shift - shift) / edge.imag if edge.imag else 0j
        along = min(1.0, max(0.0, dot(knot - a, edge) / dot(edge, edge))) if edge else 0.0
        distance = abs(knot - (a + along * edge))
        # Work in the same scaled coordinates as the offset fitter. Use the
        # moves to size the neighbourhood when the join is a single knot.
        reach = 1.5 * max(abs(edge), 2 * abs(shift), 2 * abs(end_shift))
        t = min(1.0, max(0.0, 2 - 2 * distance / reach)) if reach else 0.0
        influence = t * t * (3 - 2 * t)
        # Both handles inherit their knot's frame to preserve the tangent.
        # Beyond the reach the fallback is the Regular curve.
        frames.append((a, influence * shift, influence * slope))
    fallback = []
    for i, curve in enumerate(regular):
        middle = len(curve)//2
        points = []
        for controls, (origin, shift, slope) in ((curve[:middle], frames[i]),
                                                 (curve[middle:], frames[(i+1) % len(frames)])):
            points.extend(p + shift + slope*(p-origin).imag for p in controls)
        fallback.append(tuple(points))
    return fit_contour(regular, proposed, fallback) or fitted
