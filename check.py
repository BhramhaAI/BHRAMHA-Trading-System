import pandas as pd

df = pd.read_csv('signals_log.csv')
mask = df['result'].isin(['WIN', 'LOSS'])
print(df[mask][['coin', 'time', 'close_time', 'tp', 'sl', 'entry']].to_string())