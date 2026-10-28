"""Display-geometry contracts using synthetic arrays; no dynamics or device."""
import types
import unittest
import numpy as np

from mapping_collision import (NativeGeometryEngine,GeometryBudgetExceeded,
    geometry_layout,finger_step_scales,PAIR_COUNT,MIN_CLEARANCE_M)


def tree_model():
    parent=[0,0];first=[-1,-1];count=[0,0];weld=[0,1]
    bodies=[1]
    for finger in range(5):
        branch=[]
        for axis in range(4):
            body=len(parent);parent.append(branch[-1] if branch else 1)
            first.append(4*finger+axis);count.append(1);weld.append(body);branch.append(body)
        bodies.extend(branch+[branch[-1]])
    return types.SimpleNamespace(nu=20,nq=20,nv=20,ngeom=52,
        actuator_trnid=np.array([(joint,0) for joint in range(20)]),
        geom_group=[0]*26+[1]*26,geom_bodyid=bodies+bodies,
        body_parentid=parent,body_jntadr=first,body_jntnum=count,body_weldid=weld)


def synthetic_engine():
    engine=NativeGeometryEngine.__new__(NativeGeometryEngine)
    engine.np=np;engine.clock=lambda:0.;engine.closed=False;engine.ranges=np.array([[-1.,1.]]*20)
    engine.q=np.zeros(20);engine.total_queries=0;engine.separators=np.zeros((290,3))
    engine.pair_radii=np.zeros((290,20));engine.pair_fingers=[(0,1)]*290
    engine.coverage_sha256='a'*64;engine.visual_pairs=tuple((i,i+26) for i in range(26))
    engine._clearances=lambda q,deadline=None:np.full(290,.01)
    return engine


class MappingGeometryTests(unittest.TestCase):
    def test_pair_tree_covers_cross_palm_distal_and_nonadjacent_same_finger(self):
        model=tree_model();geometry,pairs,joints=geometry_layout(model)
        self.assertEqual(len(geometry),26);self.assertEqual(joints,list(range(20)))
        self.assertEqual(len(pairs),PAIR_COUNT)
        self.assertEqual({kind:sum(row[2]==kind for row in pairs) for kind in set(p[2] for p in pairs)},
            dict(cross_finger=250,palm_distal=15,same_finger_nonadjacent=25))
        for a,b,kind in pairs:
            if kind=='same_finger_nonadjacent':
                self.assertEqual(a['finger'],b['finger'])
                self.assertGreaterEqual(abs(a['depth']-b['depth']),2)
        # A model's old collision masks cannot remove a required pair.
        model.geom_contype=[0]*52;model.geom_conaffinity=[0]*52
        self.assertEqual(len(geometry_layout(model)[1]),290)

    def test_missing_geom_or_ambiguous_axis_tree_fails_closed(self):
        model=tree_model();model.ngeom=25
        with self.assertRaises(ValueError):geometry_layout(model)
        model=tree_model();model.actuator_trnid[1,0]=0
        with self.assertRaises(ValueError):geometry_layout(model)

    def test_per_finger_bound_leaves_unrelated_fingers_free(self):
        scales=finger_step_scales([.001],[.01],[(0,1)])
        self.assertAlmostEqual(scales[0],.0784);self.assertEqual(scales[0],scales[1])
        self.assertEqual(scales[2:],[1.,1.,1.])
        for values,bounds,fingers in (([],[],[]),([.001],[],[]),([float('nan')],[1],[(0,1)]),
                ([0.],[1],[(0,1)]),([.001],[-1],[(0,1)])):
            with self.assertRaises(ValueError):finger_step_scales(values,bounds,fingers)

    def test_hull_subset_can_nominate_but_never_certify_missing_actual_vertices(self):
        engine=synthetic_engine();engine.hull_indices={0:np.array([0]),1:np.array([0])}
        world={0:np.array([[0.,0,0],[2.,0,0]]),1:np.array([[1.5,0,0],[3.,0,0]])}
        distance,_=engine._verified_axes(0,1,[[1.,0,0]],world)
        self.assertEqual(distance,0.)
        world[1]+=np.array([2.,0,0])
        distance,_=engine._verified_axes(0,1,[[1.,0,0]],world)
        self.assertGreater(distance,1.49)

    def test_deadline_is_checked_inside_direction_batches(self):
        engine=synthetic_engine();engine.clock=lambda:1.
        engine.hull_indices={0:np.array([0]),1:np.array([0])}
        with self.assertRaises(GeometryBudgetExceeded):
            engine._verified_axes(0,1,[[1.,0,0]],{0:np.zeros((1,3)),1:np.ones((1,3))},.01)

    def test_tiny_limit_overshoot_changes_only_display_goal(self):
        engine=synthetic_engine();raw=np.array([1.00001]+[.05]*19);before=raw.tobytes()
        result=engine.step(raw,12)
        self.assertEqual(raw.tobytes(),before)
        self.assertTrue(result['display_target_clamped']);self.assertAlmostEqual(result['raw_limit_excess_rad'],.00001)
        self.assertEqual(result['limit_excess_rad'],0.)
        self.assertTrue(result['frame_verified']);self.assertEqual(result['geometry_pairs_verified'],290)
        for target in ([float('nan')]*20,[1.]*19,[1.01]+[0.]*19):
            with self.assertRaises(ValueError):engine.step(target,12)

    def test_incomplete_initial_geometry_cannot_be_a_fresh_constrained_frame(self):
        engine=synthetic_engine()
        def timeout(*_):raise GeometryBudgetExceeded()
        engine._clearances=timeout
        result=engine.step([.1]*20,12)
        self.assertFalse(result['frame_verified']);self.assertEqual(result['geometry_pairs_verified'],0)
        self.assertFalse(result['progressed']);self.assertTrue(result['budget_limited'])
        self.assertEqual(result['display_q'],[0.]*20)

    def test_partial_candidate_restores_last_verified_pose_and_separators(self):
        engine=synthetic_engine();calls=[]
        def clear(q,deadline=None):
            calls.append(1)
            if len(calls)>1:
                engine.separators[:]=99.
                raise GeometryBudgetExceeded()
            return np.full(290,.01)
        engine._clearances=clear
        result=engine.step([.1]*20,12)
        self.assertTrue(result['frame_verified']);self.assertTrue(result['budget_limited'])
        self.assertFalse(result['progressed']);self.assertEqual(result['display_q'],[0.]*20)
        self.assertTrue(np.array_equal(engine.separators,np.zeros((290,3))))
        self.assertEqual(result['geometry_pairs_verified'],290)


if __name__=='__main__':unittest.main()
