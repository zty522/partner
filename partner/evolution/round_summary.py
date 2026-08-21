
import os, json, time, glob

def save_round_summary(workspace, title, step_count):
    """Save round summary for next iteration's context."""
    try:
        summary_dir = os.path.join(workspace, "partner_data")
        os.makedirs(summary_dir, exist_ok=True)
        summary_path = os.path.join(summary_dir, "last_round_summary.txt")
        
        summary = f"Round: {title[:80]}\n"
        summary += f"Steps: {step_count}\n"
        summary += f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        
        # Find output files
        for pattern in ["*.csv", "*.md", "*.pdf"]:
            for f in glob.glob(os.path.join(workspace, "**", pattern), recursive=True)[:2]:
                if os.path.getsize(f) > 100:
                    summary += f"Output: {os.path.relpath(f, workspace)} ({os.path.getsize(f)}b)\n"
        
        with open(summary_path, "w") as f:
            f.write(summary)
        return True
    except:
        return False
