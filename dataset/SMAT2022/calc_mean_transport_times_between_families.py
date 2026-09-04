import pandas as pd
import itertools
from joblib import Parallel, delayed, cpu_count
import numpy as np
import time

transport_times_csv = 'transport_times.csv'
equipment_csv = 'Equipment.csv'
toolgroup_col = 'TOOL_GROUP'
transport_time_col = 'TRANSPORT_TIME'

transport_times_df = pd.read_csv(transport_times_csv, delimiter='\t')
equipment_df = pd.read_csv(equipment_csv, delimiter='\t')

toolgroups = set(equipment_df[toolgroup_col])

all_combinations = list(itertools.product(toolgroups, repeat=2))
filtered_combinations = [combo for combo in all_combinations if combo[0] != combo[1]]

# We divide the route list for faster processing:
start_time = time.time()

def process_chunk(filtered_combinations_chunk):
    travel_times_between_groups = []
    for start_toolgroup, destination_toolgoup in filtered_combinations_chunk:
        start_tools = equipment_df.loc[equipment_df[toolgroup_col] == start_toolgroup, 'NAME']
        destination_tools = equipment_df.loc[equipment_df[toolgroup_col] == destination_toolgoup, 'NAME']
        links_between_groups = transport_times_df[
            transport_times_df['FROM'].isin(start_tools) & transport_times_df['TO'].isin(destination_tools)]
        mean_travel_time = links_between_groups[transport_time_col].mean()
        travel_times_between_groups.append(
            {'FROM': start_toolgroup, 'TO': destination_toolgoup, transport_time_col: mean_travel_time})
    return travel_times_between_groups

num_splits = cpu_count()
chunks = np.array_split(filtered_combinations, num_splits)
processed_chunks = Parallel(n_jobs=-1)(delayed(process_chunk)(chunk) for chunk in chunks)

travel_times_between_groups = list(itertools.chain.from_iterable(processed_chunks))
end_time = time.time()
duration = end_time - start_time
print(f"...parallelized code completed in {'{:.2f}'.format(duration / 60)} minutes on {num_splits} CPUs.")

result_df = pd.DataFrame(travel_times_between_groups)
result_df.to_csv('transport_times_between_tool_groups.csv', sep='\t', index=False)