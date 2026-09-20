// Pure display mathematics; no device, DOM or network access.
export function cameraGesture(camera,kind,dx,dy){
  const finite=x=>typeof x==='number'&&Number.isFinite(x);
  if(!camera||!Array.isArray(camera.lookat)||camera.lookat.length!==3||
     ![camera.azimuth,camera.elevation,camera.distance,...camera.lookat,dx,dy].every(finite))throw TypeError('Invalid camera');
  const clamp=(x,a,b)=>Math.max(a,Math.min(b,x));
  const wrap=x=>((x%360)+360)%360;
  const c={azimuth:wrap(camera.azimuth),elevation:clamp(camera.elevation,-85,85),
    distance:clamp(camera.distance,.16,.9),lookat:camera.lookat.map(x=>clamp(x,-.4,.4))};
  if(kind==='orbit'){
    c.azimuth=wrap(c.azimuth+(dx%2)*180);c.elevation=clamp(c.elevation-clamp(dy,-2,2)*180,-85,85);
  }else if(kind==='zoom')c.distance=clamp(c.distance*Math.exp(clamp(dy,-1,1)),.16,.9);
  else if(kind==='pan'){
    const a=c.azimuth*Math.PI/180,e=c.elevation*Math.PI/180;
    const horizontal=[-Math.sin(a),Math.cos(a),0],vertical=[-Math.sin(e)*Math.cos(a),-Math.sin(e)*Math.sin(a),Math.cos(e)];
    // Canvas gestures are naturally small; bounding their magnitude also
    // prevents finite but enormous synthetic displacements from overflowing.
    const x=clamp(dx,-10,10),y=clamp(dy,-10,10);
    c.lookat=c.lookat.map((p,i)=>clamp(p+c.distance*2*(-x*horizontal[i]+y*vertical[i]),-.4,.4));
  }else throw RangeError('Unsupported gesture');
  return c;
}
