"""Conservative geometry for a display, never a device target or feedback writer.

Uses the renderer's existing MuJoCo runtime and official convex mesh geometry.
There is no integration, dynamics, IK, SDK import, socket or device command.
Every delivered pose verifies cross-finger, palm/distal, and nonadjacent
same-finger geoms, including every visual mesh as an identical duplicate.
Adjacent mechanical joints and the palm's proximal mountings are not a claim
of collision coverage. Original masks are irrelevant to explicit geom queries.
"""
import hashlib
import json
import math
from pathlib import Path
import time
import xml.etree.ElementTree as ET

COVERAGE='complete_nonadjacent_finger_and_palm_distal'
ALGORITHM='conservative_native_geometry_v1'
PAIR_COUNT=290
MIN_CLEARANCE_M=.0001
PATH_CLEARANCE_M=.00002
DISTANCE_CUTOFF_M=.002
MAX_FRAME_ANGLE_RAD=.12
MAX_ITERATIONS=12
MAX_FRAME_MS=18.
MAX_DISPLAY_LIMIT_CLAMP_RAD=.001
GEOMETRY_ROUNDING_PAD_M=1e-8


class GeometryBudgetExceeded(RuntimeError):
    """A partial geometry query cannot authorize a new display pose."""


def geometry_layout(model):
    """Identify all geometry by the actual 20-axis tree, not a name substring."""
    if model.nu!=20 or model.nq!=20 or model.nv!=20:
        raise ValueError('Mapping preview requires the known twenty-axis model')
    actuator_ids=[int(j) for j in model.actuator_trnid[:,0]]
    if len(set(actuator_ids))!=20:raise ValueError('Ambiguous mapping preview actuator order')
    actuator_map={j:i for i,j in enumerate(actuator_ids)}
    geometry=[]
    for geom in range(model.ngeom):
        # Official visual meshes are duplicates of the mechanical meshes.
        if int(model.geom_group[geom])==1:continue
        body=int(model.geom_bodyid[geom]);node=body;ancestors=[]
        while node:
            first=int(model.body_jntadr[node]);count=int(model.body_jntnum[node])
            for joint in range(first,first+count):
                if joint not in actuator_map:raise ValueError('Unclassified mapping preview joint')
                ancestors.append(actuator_map[joint])
            node=int(model.body_parentid[node])
        fingers={index//4 for index in ancestors}
        if len(fingers)>1:raise ValueError('Mapping preview geometry crosses finger trees')
        geometry.append(dict(geom=geom,body=body,finger=next(iter(fingers),None),depth=len(ancestors)))
    if (len(geometry)!=26 or sum(g['finger'] is None for g in geometry)!=1
            or any(sum(g['finger']==f for g in geometry)!=5 for f in range(5))):
        raise ValueError('Mapping preview geometry coverage differs from the supported model')
    pairs=[]
    for index,a in enumerate(geometry):
        for b in geometry[index+1:]:
            cross=a['finger'] is not None and b['finger'] is not None and a['finger']!=b['finger']
            palm=(a['finger'] is None and b['depth']>=3) or (b['finger'] is None and a['depth']>=3)
            same=False
            if a['finger'] is not None and a['finger']==b['finger']:
                wa=int(model.body_weldid[a['body']]);wb=int(model.body_weldid[b['body']])
                pa=int(model.body_weldid[int(model.body_parentid[wa])])
                pb=int(model.body_weldid[int(model.body_parentid[wb])])
                same=wa!=wb and wa!=pb and wb!=pa
            if cross or palm or same:
                pairs.append((a,b,'cross_finger' if cross else 'palm_distal' if palm else 'same_finger_nonadjacent'))
    if (len(pairs)!=PAIR_COUNT or sum(p[2]=='cross_finger' for p in pairs)!=250
            or sum(p[2]=='same_finger_nonadjacent' for p in pairs)!=25):
        raise ValueError('Mapping preview pair coverage is incomplete')
    return geometry,pairs,actuator_ids


def finger_step_scales(distances,movement_bounds,pair_fingers):
    """Per-finger conservative advancement; unrelated fingers keep moving.

    A convex-set distance can decrease by no more than the sum of the two
    geometries' point-motion bounds. Each participating finger is scaled by at
    most the pair's permitted fraction, so this bound also holds after scaling.
    The separately checked endpoint keeps extra clearance for moving away from
    contact on subsequent frames. No force/contact law is invented here.
    """
    if not len(distances)==len(movement_bounds)==len(pair_fingers) or not len(distances):
        raise ValueError('Incomplete geometry clearance set')
    scales=[1.]*5
    for distance,bound,fingers in zip(distances,movement_bounds,pair_fingers):
        if not math.isfinite(float(distance)) or not math.isfinite(float(bound)) or bound<0:
            raise ValueError('Unknown geometry distance or motion bound')
        if distance<MIN_CLEARANCE_M-1e-12:raise ValueError('Previous display geometry is not separated')
        if bound<=0:continue
        fraction=min(1.,max(0.,.8*(distance-PATH_CLEARANCE_M)/bound))
        for finger in fingers:
            if type(finger) is not int or not 0<=finger<5:raise ValueError('Invalid geometry finger')
            scales[finger]=min(scales[finger],fraction)
    return scales


class NativeGeometryEngine:
    """A separate immutable-asset model and private, kinematic display state."""
    algorithm=ALGORITHM
    coverage=COVERAGE
    geometry_only=True

    def __init__(self,mj,profile,asset_root,*,clock=time.perf_counter):
        import numpy as np
        self.mj,self.np,self.profile,self.clock=mj,np,profile,clock
        if getattr(mj,'__version__',None)!='3.3.7':raise ValueError('The bundled MuJoCo 3.3.7 runtime is required')
        generation,side=profile.split('_')
        if generation not in ('hand1','hand2') or side not in ('left','right'):raise ValueError('Unsupported preview profile')
        root=Path(asset_root).resolve();path=(root/generation/'mjcf'/(side+'.xml')).resolve()
        if not path.is_relative_to(root):raise ValueError('Invalid preview model path')
        xml=ET.fromstring(path.read_bytes());compiler=xml.find('compiler')
        meshdir=(path.parent/compiler.get('meshdir')).resolve()
        if not meshdir.is_relative_to(root):raise ValueError('Invalid preview mesh directory')
        assets={}
        for mesh in xml.findall('./asset/mesh'):
            name=mesh.get('file');source=(meshdir/name).resolve()
            if not source.is_relative_to(root) or name in assets:raise ValueError('Invalid preview mesh identity')
            assets[name]=source.read_bytes()
        compiler.set('meshdir','')
        self.model_sha256=hashlib.sha256(json.dumps(dict(xml=hashlib.sha256(path.read_bytes()).hexdigest(),
            meshes={name:hashlib.sha256(data).hexdigest() for name,data in sorted(assets.items())}),sort_keys=True).encode()).hexdigest()
        # Like device_profiles.load_native_model, use Python's Unicode paths
        # for reading and feed native code an in-memory XML/asset collection.
        self.model=mj.MjModel.from_xml_string(ET.tostring(xml,encoding='unicode'),assets=assets)
        self.data=mj.MjData(self.model)
        self.visual_pairs=self._verify_visual_geometry()
        self.geometry,self.pairs,self.joint_ids=geometry_layout(self.model)
        if (int(self.model.opt.disableflags)&int(mj.mjtDisableBit.mjDSBL_NATIVECCD)
                or not 0<float(self.model.opt.ccd_tolerance)<=PATH_CLEARANCE_M/4):
            raise ValueError('Native geometry query precision is not confirmed')
        if any(int(self.model.geom_type[g['geom']])!=int(mj.mjtGeom.mjGEOM_MESH) for g in self.geometry):
            raise ValueError('Unexpected mapping preview geometry type')
        if any(int(self.model.jnt_type[j])!=int(mj.mjtJoint.mjJNT_HINGE) for j in self.joint_ids):
            raise ValueError('Unexpected mapping preview joint type')
        self.qpos_ids=np.array([int(self.model.jnt_qposadr[j]) for j in self.joint_ids])
        self.ranges=np.array([self.model.jnt_range[j] for j in self.joint_ids],dtype=float)
        self.geom_ids=np.array([g['geom'] for g in self.geometry],dtype=int)
        lookup={int(g):i for i,g in enumerate(self.geom_ids)}
        self.pair_indices=np.array([(lookup[a['geom']],lookup[b['geom']]) for a,b,_ in self.pairs],dtype=int)
        self.pair_fingers=[tuple(g['finger'] for g in (a,b) if g['finger'] is not None) for a,b,_ in self.pairs]
        self.coverage_sha256=hashlib.sha256(json.dumps([(a['geom'],b['geom'],kind) for a,b,kind in self.pairs]).encode()).hexdigest()
        self.motion_radii=self._motion_radii()
        self.pair_radii=np.array([np.where((self.motion_radii[a['geom']]>0)&(self.motion_radii[b['geom']]>0),
            0.,self.motion_radii[a['geom']]+self.motion_radii[b['geom']]) for a,b,_ in self.pairs])
        self.q=np.clip(np.zeros(20),self.ranges[:,0],self.ranges[:,1])
        self.closed=False;self.total_queries=0
        self.separators=np.zeros((len(self.pairs),3))
        self._prepare_support_geometry()
        self.distances=self._clearances(self.q)
        if self.distances.min()<MIN_CLEARANCE_M:
            raise ValueError('Official model neutral geometry is not verified as separated')

    def _verify_visual_geometry(self):
        # Group 1 is omitted from collision pairs only after a one-to-one
        # comparison of the compiled mesh ID AND the entire shape transform.
        # A visual-only mesh, extra scaling, or a different body fails closed.
        np,m=self.np,self.model
        visual=[g for g in range(m.ngeom) if int(m.geom_group[g])==1]
        physical=[g for g in range(m.ngeom) if int(m.geom_group[g])!=1]
        matches=[]
        for g in visual:
            candidates=[p for p in physical if int(m.geom_type[g])==int(m.geom_type[p])
                and int(m.geom_type[g])==int(self.mj.mjtGeom.mjGEOM_MESH)
                and int(m.geom_bodyid[g])==int(m.geom_bodyid[p])
                and int(m.geom_dataid[g])==int(m.geom_dataid[p])
                and np.array_equal(m.geom_pos[g],m.geom_pos[p])
                and np.array_equal(m.geom_quat[g],m.geom_quat[p])
                and np.array_equal(m.geom_size[g],m.geom_size[p])]
            if len(candidates)!=1:raise ValueError('Visual mesh is not identical to inspected geometry')
            matches.append((g,candidates[0]))
        if len(matches)!=26 or {p for _,p in matches}!=set(physical):
            raise ValueError('Visual geometry coverage is incomplete')
        return tuple(matches)

    def _budget(self,deadline):
        if deadline is not None and self.clock()>=deadline:raise GeometryBudgetExceeded('Geometry display budget exhausted')

    def _prepare_support_geometry(self):
        # scipy is already a product dependency. ConvexHull provides candidate
        # normals; every actual certificate still projects ALL compiled mesh
        # vertices, so neither a heuristic normal nor a hull result is trusted
        # as a separation verdict in its own right.
        from scipy.spatial import ConvexHull
        np,m=self.np,self.model;self.vertices={};self.hull_indices={};self.normals={};self.edges={}
        for gid in self.geom_ids:
            mesh=int(m.geom_dataid[gid]);start=int(m.mesh_vertadr[mesh]);count=int(m.mesh_vertnum[mesh])
            points=m.mesh_vert[start:start+count].astype(float)
            center=m.geom_aabb[gid,:3];half=m.geom_aabb[gid,3:]
            if (not np.isfinite(points).all() or np.max(np.abs(points-center)-half)>GEOMETRY_ROUNDING_PAD_M
                    or np.max(np.linalg.norm(points,axis=1))>float(m.geom_rbound[gid])+GEOMETRY_ROUNDING_PAD_M):
                raise ValueError('Compiled geometry bounds do not enclose every mesh vertex')
            hull=ConvexHull(points)
            self.vertices[int(gid)]=points
            self.hull_indices[int(gid)]=np.asarray(hull.vertices,dtype=int)
            self.normals[int(gid)]=np.unique(np.round(hull.equations[:,:3],10),axis=0)
            edge_indices={(min(int(a),int(b)),max(int(a),int(b))) for triangle in hull.simplices
                for a,b in ((triangle[0],triangle[1]),(triangle[1],triangle[2]),(triangle[2],triangle[0]))}
            edges=np.array([points[b]-points[a] for a,b in edge_indices]);lengths=np.linalg.norm(edges,axis=1)
            edges=edges[lengths>1e-10]/lengths[lengths>1e-10,None]
            self.edges[int(gid)]=np.unique(np.round(edges,10),axis=0)

    def _verified_axes(self,a,b,axes,world,deadline=None):
        np=self.np
        axes=np.asarray(axes,dtype=float).reshape(-1,3);norms=np.linalg.norm(axes,axis=1)
        axes=axes[np.isfinite(norms)&(norms>1e-10)]
        if not len(axes):return 0.,np.zeros(3)
        axes=axes/np.linalg.norm(axes,axis=1)[:,None]
        # Hull vertices only nominate a direction. The winning direction is
        # then projected over EVERY compiled vertex before it can certify a
        # clearance. This makes hull construction errors conservative.
        pa=world[a][self.hull_indices[a]];pb=world[b][self.hull_indices[b]]
        best=0.;direction=np.zeros(3)
        for start in range(0,len(axes),64):
            self._budget(deadline)
            current=axes[start:start+64]
            p=np.einsum('vi,ai->va',pa,current);q=np.einsum('vi,ai->va',pb,current)
            gaps=np.maximum(q.min(axis=0)-p.max(axis=0),p.min(axis=0)-q.max(axis=0))-1e-10
            index=int(np.argmax(gaps))
            if gaps[index]<=best:continue
            axis=current[index]
            p=np.einsum('vi,i->v',world[a],axis);q=np.einsum('vi,i->v',world[b],axis)
            gap=float(max(q.min()-p.max(),p.min()-q.max()))-1e-10
            if gap>best:best=gap;direction=axis.copy()
            if best>=MIN_CLEARANCE_M:break
        self._budget(deadline)
        return best,direction

    def _motion_radii(self):
        """Configuration-independent point-motion bounds for each hinge.

        Triangle bounds cover the geom's bounding sphere, body offsets, and
        displaced hinge anchors. Official assets are left unchanged.
        """
        np,m=self.np,self.model;indices={j:i for i,j in enumerate(self.joint_ids)};result={}
        for geom in self.geometry:
            gid=geom['geom'];node=geom['body'];r=float(m.geom_rbound[gid])+GEOMETRY_ROUNDING_PAD_M+float(np.linalg.norm(m.geom_pos[gid]))
            coefficients=np.zeros(20)
            while node:
                first=int(m.body_jntadr[node]);count=int(m.body_jntnum[node])
                for joint in range(first,first+count):
                    r+=2*float(np.linalg.norm(m.jnt_pos[joint]))
                    coefficients[indices[joint]]=r+float(np.linalg.norm(m.jnt_pos[joint]))
                r+=float(np.linalg.norm(m.body_pos[node]));node=int(m.body_parentid[node])
            result[gid]=coefficients
        return result

    def _clearances(self,q,deadline=None):
        np,m,d=self.np,self.model,self.data
        d.qpos[self.qpos_ids]=q
        self.mj.mj_kinematics(m,d)
        # Broad-phase lower bounds are conservative world AABBs of the compiled
        # geometry. Near/overlapping bounds require all-vertex separation.
        boxes=m.geom_aabb[self.geom_ids]
        rotations=d.geom_xmat[self.geom_ids].reshape(-1,3,3)
        centers=d.geom_xpos[self.geom_ids]+np.einsum('nij,nj->ni',rotations,boxes[:,:3])
        halves=np.einsum('nij,nj->ni',np.abs(rotations),boxes[:,3:]+GEOMETRY_ROUNDING_PAD_M)
        low=centers-halves;high=centers+halves
        a,b=self.pair_indices.T
        gaps=np.maximum(0,np.maximum(low[a]-high[b],low[b]-high[a]))
        distances=np.maximum(0,np.linalg.norm(gaps,axis=1)-1e-12)
        far=distances>=MIN_CLEARANCE_M
        oriented=np.maximum(0,low[b]-high[a])-np.maximum(0,low[a]-high[b])
        self.separators[far]=oriented[far]/np.linalg.norm(oriented[far],axis=1)[:,None]
        world={}
        for index in np.flatnonzero(distances<MIN_CLEARANCE_M):
            self._budget(deadline)
            p=self.pairs[int(index)]
            gid1,gid2=p[0]['geom'],p[1]['geom']
            for gid in (gid1,gid2):
                if gid not in world:world[gid]=np.einsum('vi,ji->vj',self.vertices[gid],d.geom_xmat[gid].reshape(3,3))+d.geom_xpos[gid]
            # A previously verified plane is cheap to recheck against every
            # vertex. Native witnesses nominate directions only when needed.
            distance,direction=self._verified_axes(gid1,gid2,[self.separators[index]],world,deadline)
            if distance<MIN_CLEARANCE_M:
                segment=np.zeros(6)
                native=float(self.mj.mj_geomDistance(m,d,gid1,gid2,DISTANCE_CUTOFF_M,segment))
                self.total_queries+=1
                if not math.isfinite(native):raise ValueError('Unknown native geometry query')
                candidates=[segment[3:]-segment[:3],d.geom_xpos[gid2]-d.geom_xpos[gid1]]
                distance,direction=self._verified_axes(gid1,gid2,candidates,world,deadline)
            if distance<MIN_CLEARANCE_M:
                axes=np.vstack((self.normals[gid1]@d.geom_xmat[gid1].reshape(3,3).T,
                    self.normals[gid2]@d.geom_xmat[gid2].reshape(3,3).T))
                distance,direction=self._verified_axes(gid1,gid2,axes,world,deadline)
            if distance<MIN_CLEARANCE_M:
                ea=self.edges[gid1]@d.geom_xmat[gid1].reshape(3,3).T
                eb=self.edges[gid2]@d.geom_xmat[gid2].reshape(3,3).T
                # A bounded extra search can recover an edge/edge separator.
                # Exhaustion is UNKNOWN (zero), never a collision-free verdict.
                for edge in ea[:8]:
                    candidate,axis=self._verified_axes(gid1,gid2,np.cross(edge,eb),world,deadline)
                    if candidate>distance:distance,direction=candidate,axis
                    if distance>=MIN_CLEARANCE_M:break
            # The native scalar, including a zero or capped positive result,
            # is never substituted for a constructive separation certificate.
            distances[index]=distance
            if distance>=MIN_CLEARANCE_M:self.separators[index]=direction
        if not np.isfinite(distances).all():raise ValueError('Native geometry query returned an unknown distance')
        self._budget(deadline)
        return distances

    def step(self,target,substeps):
        np=self.np
        if self.closed:raise ValueError('Geometry display is closed')
        raw_target=np.asarray(target,dtype=float)
        if raw_target.shape!=(20,) or not np.isfinite(raw_target).all():raise ValueError('Invalid mapping target')
        raw_excess=float(np.max(np.maximum(self.ranges[:,0]-raw_target,raw_target-self.ranges[:,1])))
        raw_excess=max(0.,raw_excess)
        if raw_excess>MAX_DISPLAY_LIMIT_CLAMP_RAD:raise ValueError('Mapping target is outside supported display tolerance')
        # The immutable raw target stays untouched. Only a separate display
        # goal absorbs numerical overshoot at an official joint limit.
        target=np.clip(raw_target,self.ranges[:,0],self.ranges[:,1])
        before_queries=self.total_queries;started=self.clock();deadline=started+MAX_FRAME_MS/1000;iterations=0;limited=False
        current=self.q.copy();old=self.q.copy();saved=self.separators.copy()
        try:distances=self._clearances(current,deadline)
        except GeometryBudgetExceeded:
            self.separators=saved
            return dict(display_q=current.tolist(),contacts=[],stable=True,native_warning_count=0,limit_excess_rad=0.,
                algorithm=ALGORITHM,geometry_only=True,geometry_pairs_verified=0,coverage_sha256=self.coverage_sha256,
                minimum_verified_clearance_m=None,path_clearance_m=PATH_CLEARANCE_M,frame_verified=False,budget_limited=True,
                display_modified=True,iterations=0,progressed=False,native_distance_queries=self.total_queries-before_queries,
                raw_limit_excess_rad=raw_excess,display_target_clamped=bool(raw_excess))
        path=[current.copy()]
        if distances.min()<MIN_CLEARANCE_M:raise ValueError('Previous geometry display is no longer verified')
        goal=current+np.clip(target-current,-MAX_FRAME_ANGLE_RAD,MAX_FRAME_ANGLE_RAD)
        for _ in range(min(MAX_ITERATIONS,max(1,int(substeps)))):
            if self.clock()>=deadline:limited=True;break
            delta=goal-current
            if np.max(np.abs(delta))<1e-7:break
            scales=np.array(finger_step_scales(distances,self.pair_radii@np.abs(delta),self.pair_fingers))
            proposed=delta*np.repeat(scales,4)
            accepted=False
            for _ in range(10):
                if np.max(np.abs(proposed))<1e-9:break
                candidate=current+proposed
                saved=self.separators.copy()
                try:checked=self._clearances(candidate,deadline)
                except GeometryBudgetExceeded:
                    self.separators=saved;limited=True;break
                if checked.min()>=MIN_CLEARANCE_M:
                    current,distances=candidate,checked;path.append(current.copy());accepted=True;iterations+=1;break
                self.separators=saved
                # The same certified prefix shortened further stays certified.
                proposed*=.5
                if self.clock()>=deadline:limited=True;break
            if not accepted:break
        # The accepted endpoint (or initial held pose) already completed a
        # fresh check of all required pairs this frame. A partial candidate
        # never changes current or its certificate.
        final=distances
        if final.min()<MIN_CLEARANCE_M:raise ValueError('Final geometry display did not pass every required pair')
        self.q=current.copy();self.distances=final;self.last_path=tuple(tuple(map(float,q)) for q in path)
        near=[]
        for index in np.flatnonzero(final<.001):
            a,b,kind=self.pairs[int(index)]
            near.append(dict(geom1=a['geom'],geom2=b['geom'],distance_m=float(final[index]),cross_finger=kind=='cross_finger'))
        return dict(display_q=current.tolist(),contacts=near,stable=True,native_warning_count=0,limit_excess_rad=0.,
            algorithm=ALGORITHM,geometry_only=True,geometry_pairs_verified=PAIR_COUNT,coverage_sha256=self.coverage_sha256,
            minimum_verified_clearance_m=float(final.min()),path_clearance_m=PATH_CLEARANCE_M,
            display_modified=bool(np.max(np.abs(current-raw_target))>1e-7),iterations=iterations,frame_verified=True,
            raw_limit_excess_rad=raw_excess,display_target_clamped=bool(raw_excess),budget_limited=limited,
            progressed=bool(np.max(np.abs(current-old))>1e-7),visual_meshes_verified=len(self.visual_pairs),
            native_distance_queries=self.total_queries-before_queries,path_certified=True)

    def close(self):self.closed=True
