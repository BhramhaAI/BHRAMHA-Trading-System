import mplfinance as mpf
import matplotlib.pyplot as plt
import glob
import os


MAX_IMAGES = 50


def cleanup_chart_images():
    os.makedirs("charts", exist_ok=True)
    files = sorted(
        glob.glob("charts/*.png"),
        key=os.path.getmtime
    )

    if len(files) > MAX_IMAGES:
        for f in files[:-MAX_IMAGES]:
            try:
                os.remove(f)
            except FileNotFoundError:
                pass

def create_chart(df, coin, tf, entry, sl, tp, chart_tag=None):

    df = df.tail(120)

    apds = [
        mpf.make_addplot([entry]*len(df), color='blue'),
        mpf.make_addplot([sl]*len(df), color='red'),
        mpf.make_addplot([tp]*len(df), color='green')
    ]

    os.makedirs("charts", exist_ok=True)
    suffix = f"_{chart_tag}" if chart_tag else ""
    filename = os.path.join("charts", f"chart_{coin}_{tf}{suffix}.png")

    mpf.plot(
        df,
        type='candle',
        style='charles',
        title=f"{coin} {tf}",
        addplot=apds,
        volume=True,
        savefig=filename
    )

    return filename
