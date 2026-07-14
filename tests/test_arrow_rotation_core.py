import math
import pathlib
import sys
import unittest


PACKAGE = pathlib.Path(__file__).resolve().parents[1] / "toolbox"
sys.path.insert(0, str(PACKAGE))

from arrow_rotation_core import (  # noqa: E402
    Endpoint,
    EndpointIndex,
    clockwise_angle_from_east,
    endpoints_from_part,
)


class AngleTests(unittest.TestCase):
    def test_cardinal_directions(self):
        self.assertEqual(clockwise_angle_from_east(1, 0), 0)
        self.assertEqual(clockwise_angle_from_east(0, -1), 90)
        self.assertEqual(clockwise_angle_from_east(-1, 0), 180)
        self.assertEqual(clockwise_angle_from_east(0, 1), 270)

    def test_diagonals(self):
        self.assertEqual(clockwise_angle_from_east(1, -1), 45)
        self.assertEqual(clockwise_angle_from_east(-1, -1), 135)

    def test_zero_vector_is_invalid(self):
        with self.assertRaises(ValueError):
            clockwise_angle_from_east(0, 0)


class EndpointTests(unittest.TestCase):
    def test_start_and_end_vectors_point_toward_endpoint(self):
        endpoints = endpoints_from_part(7, 0, [(0, 0), (2, 1), (5, 1)])
        self.assertEqual(endpoints[0].endpoint, "START")
        self.assertEqual((endpoints[0].dx, endpoints[0].dy), (-2, -1))
        self.assertEqual(endpoints[1].endpoint, "END")
        self.assertEqual((endpoints[1].dx, endpoints[1].dy), (3, 0))

    def test_repeated_terminal_vertices_are_skipped(self):
        endpoints = endpoints_from_part(7, 0, [(0, 0), (0, 0), (2, 0), (2, 0)])
        self.assertEqual((endpoints[0].dx, endpoints[0].dy), (-2, 0))
        self.assertEqual((endpoints[1].dx, endpoints[1].dy), (2, 0))

    def test_degenerate_and_closed_parts_have_no_endpoints(self):
        self.assertEqual(endpoints_from_part(1, 0, [(0, 0)]), [])
        self.assertEqual(endpoints_from_part(1, 0, [(0, 0), (1, 0), (0, 0)]), [])


class IndexTests(unittest.TestCase):
    @staticmethod
    def endpoint(line_oid, x, y, dx=1, dy=0):
        return Endpoint(line_oid, 0, "END", x, y, dx, dy)

    def test_nearest_endpoint_matches(self):
        index = EndpointIndex(
            [self.endpoint(1, 0, 0, 0, -1), self.endpoint(2, 9, 9)], 5
        )
        match = index.match(1, 1)
        self.assertEqual(match.status, "MATCHED")
        self.assertEqual(match.endpoint.line_oid, 1)
        self.assertAlmostEqual(match.distance, math.sqrt(2))
        self.assertEqual(match.rotation, 90)

    def test_unmatched_point(self):
        index = EndpointIndex([self.endpoint(1, 0, 0)], 2)
        self.assertEqual(index.match(3, 0).status, "UNMATCHED")

    def test_exact_tie_is_ambiguous(self):
        index = EndpointIndex(
            [self.endpoint(1, -1, 0), self.endpoint(2, 1, 0)], 2
        )
        match = index.match(0, 0)
        self.assertEqual(match.status, "AMBIGUOUS")
        self.assertIsNone(match.rotation)

    def test_boundary_in_adjacent_grid_cell_is_found(self):
        index = EndpointIndex([self.endpoint(1, 10.1, 0)], 10)
        self.assertEqual(index.match(9.9, 0).status, "MATCHED")

    def test_invalid_tolerance(self):
        with self.assertRaises(ValueError):
            EndpointIndex([], 0)

    def test_large_sparse_index(self):
        endpoints = [self.endpoint(i, i * 10.0, i * 3.0) for i in range(10_000)]
        index = EndpointIndex(endpoints, 2)
        self.assertEqual(index.endpoint_count, 10_000)
        for i in range(0, 10_000, 97):
            match = index.match(i * 10.0 + 0.25, i * 3.0)
            self.assertEqual(match.endpoint.line_oid, i)


if __name__ == "__main__":
    unittest.main()
