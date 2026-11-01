"""Case-study plotting uses the same anterior-positive AP convention."""
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import render_real_atlas_case_studies as render


@pytest.mark.parametrize('ap', [-2., 2.])
def test_region_plot_fallback_maps_ap_to_ccf(ap):
    s = SimpleNamespace(PLANNER_REGIONS={'x': dict(id=1, ap=ap, dv=3., ml=-1.)},
                        REGION_CENTROIDS_UM={}, BREGMA_UM=[5400., 440., 5700.])
    np.testing.assert_allclose(render.region_center_vox(s, 'x'),
                               np.array([5400.-ap*1000., 3440., 4700.])/25.)


def test_region_plot_prefers_actual_centroid():
    s = SimpleNamespace(PLANNER_REGIONS={'x': dict(id=1, ap=-2., dv=3., ml=-1.)},
                        REGION_CENTROIDS_UM={1: [6000., 4000., 5000.]},
                        BREGMA_UM=[5400., 440., 5700.])
    np.testing.assert_allclose(render.region_center_vox(s, 'x'), [240., 160., 200.])
