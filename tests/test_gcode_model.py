import unittest

from backend.gcode_model import parse_program


class GCodeModelTests(unittest.TestCase):
    def test_incremental_units_and_z_are_normalized(self):
        model = parse_program(["G20 G90", "G0 X1 Y2 Z0", "G91", "G1 X1 Z-0.25 F10"])
        last = model["segments"][-1]
        self.assertAlmostEqual(last[2], 50.8)
        self.assertAlmostEqual(last[3], 50.8)
        self.assertAlmostEqual(last[6], 0.0)
        self.assertAlmostEqual(last[7], -6.35)
        self.assertAlmostEqual(last[8], 254.0)

    def test_arc_is_sampled_and_closes_at_target(self):
        model = parse_program(["G0 X10 Y0", "G3 X0 Y10 I-10 J0"])
        arc = [s for s in model["segments"] if s[4] == "arc"]
        self.assertGreater(len(arc), 2)
        self.assertAlmostEqual(arc[-1][2], 0.0, places=5)
        self.assertAlmostEqual(arc[-1][3], 10.0, places=5)

    def test_modal_linear_move_uses_prior_motion(self):
        model = parse_program(["G1 X5", "X8"])
        self.assertEqual(len(model["segments"]), 2)
        self.assertAlmostEqual(model["segments"][-1][2], 8.0)
        self.assertTrue(any(w["code"] == "modal_motion" for w in model["warnings"]))

    def test_radius_arc_is_not_reduced_to_a_chord(self):
        model = parse_program(["G0 X10 Y0", "G3 X0 Y10 R10"])
        arc = [s for s in model["segments"] if s[4] == "arc"]
        self.assertGreater(len(arc), 2)
        self.assertAlmostEqual(arc[-1][2], 0.0, places=5)
        self.assertAlmostEqual(arc[-1][3], 10.0, places=5)


if __name__ == "__main__":
    unittest.main()
