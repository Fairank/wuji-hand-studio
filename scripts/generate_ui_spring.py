"""Generate static interface easing; never part of device control or a frame loop."""
import argparse
from pathlib import Path
from ui_spring import spring_samples, css_linear

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--check',action='store_true');args=parser.parse_args()
    path=Path(__file__).resolve().parents[1]/'src/web/spring_motion.css'
    data=path.read_text(encoding='utf-8')
    start='/* Generated spring: begin */';end='/* Generated spring: end */'
    before,tail=data.split(start,1);_,after=tail.split(end,1)
    points=spring_samples(damping_ratio=.72,frequency_hz=3.5,duration_s=.45,count=61)
    assert points[0]==0 and points[-1]==1 and 1<max(points)<1.05
    curve=css_linear(points)
    generated=f'{start}\n@supports (animation-timing-function: linear(0, 1)) {{\n  :root {{ --wb-spring: {curve}; }}\n}}\n{end}'
    expected=before+generated+after
    if args.check:
        if data!=expected:raise SystemExit('Regenerate UI spring before packaging')
    else:path.write_text(expected,encoding='utf-8',newline='\n')
    print(f'UI spring: {len(points)} samples, {max(points)-1:.2%} relative overshoot; generated asset verified')

if __name__=='__main__':main()
