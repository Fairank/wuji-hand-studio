"""Small read-only summaries. Legacy failed-trial displacement is not return accuracy."""
FIELDS=('id','kind','action','amplitude','requested_cycles','finished_cycles','accepted','completed',
        'stop_confirmed','reason','started_unix','peak_current_A','stopped_phase','stopped_action_phase',
        'peak_actual_delta_deg','schema')


def summarize(report):
    summary={key:report[key] for key in FIELDS if key in report}
    full=report.get('kind')=='low_current_showcase_trial'
    evaluated=report.get('return_evaluated',bool(report.get('completed')) if full else True)
    summary['return_evaluated']=evaluated
    summary['return_error_deg']=report.get('return_error_deg') if evaluated else None
    summary['displacement_at_stop_deg']=report.get('displacement_at_stop_deg',report.get('return_error_deg'))
    return summary
