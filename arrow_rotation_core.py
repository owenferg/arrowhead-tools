"""
pure python geometry and matching logic for arrowheads

I intentionally didnt include any arcpy dependency so
the direction math and spatial matching can be tested in any Python runtime.
Angles use clockwise-from-east: east = 0, positive values rotate clockwise.
"""

from __future__ import annotations
from dataclasses import dataclass
import math
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

PointXY = Tuple[float, float]

@dataclass(frozen=True)
class Endpoint:
    '''usable line endpoint and the terminal vector pointing toward it'''

    line_oid: int
    part_index: int
    endpoint: str
    x: float
    y: float
    dx: float
    dy: float


@dataclass(frozen=True)
class Match:
    '''result of matching one arrowhead to the endpoint index'''

    status: str
    endpoint: Optional[Endpoint] = None
    distance: Optional[float] = None
    rotation: Optional[float] = None


def clockwise_angle_from_east(dx: float, dy: float) -> float:
    '''return a normalized clockwise angle where east is zero degrees'''

    if dx == 0 and dy == 0:
        raise ValueError('A zero-length vector has no direction')

    # angle equals the angle of the vector from the x-axis to the vector,
    # normalized to the range 0-360 degrees
    angle = math.degrees(math.atan2(-dy, dx)) % 360.0

    # return zero if the angle is close to 360 degrees
    return 0.0 if math.isclose(angle, 360.0) else angle

def endpoints_from_part(
    line_oid: int,
    part_index: int,
    points: Sequence[PointXY],
) -> List[Endpoint]:
    '''create start/end records, skipping repeated terminal coordinates'''

    if len(points)<2 or points[0] == points[-1]:
        return []

    start = points[0] # first point
    start_neighbor = next((point for point in points[1:] if point != start), None) # second point
    end = points[-1] # last point
    end_neighbor = next((point for point in reversed(points[:-1]) if point != end), None) # second-to-last point

    records: List[Endpoint] = []
    if start_neighbor is not None: # if there is a second point, add a start record
        records.append(
            Endpoint(
                line_oid, part_index, 'START', start[0], start[1],
                start[0] - start_neighbor[0], start[1] - start_neighbor[1],
            )
        )
    if end_neighbor is not None: # if there is a second-to-last point, add an end record
        records.append(
            Endpoint(
                line_oid, part_index, 'END', end[0], end[1],
                end[0] - end_neighbor[0], end[1] - end_neighbor[1],
            )
        )
    return records

class EndpointIndex:
    '''uniform grid endpoint index with bounded nearest-neighbor queries'''

    def __init__(self, endpoints: Iterable[Endpoint], search_tolerance: float):

        if not math.isfinite(search_tolerance) or search_tolerance <= 0:
            raise ValueError('Search tolerance must be a positive finite number')
        
        self.search_tolerance = search_tolerance
        self._cells: Dict[Tuple[int, int], List[Endpoint]] = {}
        self.endpoint_count = 0

        for ep in endpoints:
            self._cells.setdefault(self._cell(ep.x, ep.y), []).append(ep)
            self.endpoint_count += 1

    def _cell(self, x: float, y: float) -> Tuple[int, int]:
        '''cell coordinates for a point within the index'''
        size = self.search_tolerance
        return math.floor(x / size), math.floor(y / size)

    def match(self, x: float, y: float) -> Match:
        '''find one unambiguous nearest endpoint within the configured radius'''

        cell_x, cell_y = self._cell(x,y)
        tolerance_sq = self.search_tolerance * self.search_tolerance
        candidates: List[Tuple[float, Endpoint]] = []

        for offset_x in (-1, 0, 1):
            for offset_y in (-1, 0, 1):
                for ep in self._cells.get(
                    (cell_x + offset_x, cell_y + offset_y), ()
                ):
                    distance_sq = (x - ep.x)**2 + (y - ep.y)**2

                    if distance_sq <= tolerance_sq:
                        candidates.append((distance_sq, ep))
        
        if not candidates:
            # if no candidates, return unmatchable
            return Match('UNMATCHED')

        candidates.sort(key=lambda item: item[0]) # sort by distance squared
        best_distance_sq, best = candidates[0] # best candidate is first
        tie_epsilon = max(1e-18, tolerance_sq * 1e-12) # epsilon for tie breaking

        if len(candidates) > 1 and math.isclose(
            # if there are multiple candidates, check for a tie
            candidates[1][0], best_distance_sq, rel_tol=1e-12,
            abs_tol=tie_epsilon,
        ):
            return Match('AMBIGUOUS', distance=math.sqrt(best_distance_sq))

        # return the best match
        return Match(
            'MATCHED',
            endpoint=best,
            distance=math.sqrt(best_distance_sq),
            rotation=clockwise_angle_from_east(best.dx, best.dy),
        )