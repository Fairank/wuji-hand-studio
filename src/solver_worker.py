"""JSON-line optimizer process; accepts skeleton frames, never robot commands."""
import contextlib,json,sys

def main():
    solver=None
    for raw in sys.stdin:
        if len(raw)>65536:raise ValueError('Oversized solver request')
        request=json.loads(raw);ident=request.get('id')
        try:
            with contextlib.redirect_stdout(sys.stderr):
                if request.get('op')=='init':
                    from solver_core import OfficialSolver
                    replacement=OfficialSolver(request['profile'],request['values']);solver=replacement
                    result=dict(info=solver.info())
                elif request.get('op')=='step' and solver:
                    result=dict(q=solver.step(request['points']),info=solver.info())
                else:raise ValueError('Unknown solver operation')
            response=dict(ok=True,id=ident,**result)
        except Exception as error:response=dict(ok=False,id=ident,error=str(error)[:500])
        print(json.dumps(response,allow_nan=False),flush=True)

if __name__=='__main__':main()
