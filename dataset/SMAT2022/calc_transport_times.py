import pandas as pd
import itertools
import networkx as nx
import math
import numpy as np
from joblib import Parallel, delayed, cpu_count
import time

def calculate_edge_travel_time(distance, speed_limit, initial_speed, speed_limit_next_edge):

    def calc_v_max_with_v_0_and_v_end(a, d, s, v_0, v_end):
        """
        Calculates the maximum speed of a vehicle given its acceleration and deceleration
        over a specified distance, taking into account an initial and final speed.

        :param a: Acceleration
        :param d: Deceleration
        :param s: Total distance
        :param v_0: Initial speed
        :param v_end: Final speed
        :return: Maximum speed
        """


        # Adjusting the formula v^2 = v0^2 + 2as for s
        s_eff = s - (v_end ** 2 - v_0 ** 2) / (2 * a) - (v_end ** 2 - v_0 ** 2) / (2 * d)

        # Calculating v_max assuming the entire effective distance is used for both acceleration and deceleration
        v_max = math.sqrt(v_0 ** 2 + 2 * a * s_eff * d / (a + d))

        return v_max

    achievable_speed = min(max_speed, speed_limit)

    # Calculate time and distance to accelerate to achievable speed
    acceleration_time = (achievable_speed - initial_speed) / acceleration
    acceleration_distance = initial_speed * acceleration_time + 0.5 * acceleration * acceleration_time ** 2

    # Calculate time and distance to decelerate to the speed limit of the next edge
    deceleration_time = (achievable_speed - speed_limit_next_edge) / deceleration
    deceleration_distance = achievable_speed * deceleration_time - 0.5 * deceleration * deceleration_time ** 2

    distance_cruise = distance - acceleration_distance - deceleration_distance
    if distance_cruise < 0:
        time_cruise = 0
        v_max = calc_v_max_with_v_0_and_v_end(acceleration, deceleration, distance, initial_speed, speed_limit_next_edge)
        acceleration_time = (v_max - initial_speed) / acceleration
        deceleration_time = (speed_limit_next_edge - v_max) / deceleration

    time_cruise = distance_cruise / achievable_speed if distance_cruise > 0 else 0
    total_time = acceleration_time + time_cruise + deceleration_time
    return total_time  # in seconds

def _calc_travel_time_row(row):
    from_equipment = row['FROM']
    to_equipment = row['TO']

    def get_port(equipment):
        associated_ports = ports[ports['EQP_NAME'] == equipment]
        if len(associated_ports['RAILLINE_NAME'].unique()) > 1:
            raise ValueError(f'{equipment} has more than one rail associated to it')
        return associated_ports.index[0]

    from_port, to_port = tuple(get_port(equipment) for equipment in [from_equipment, to_equipment])

    from_rail = ports.loc[from_port, 'RAILLINE_NAME']
    to_rail = ports.loc[to_port, 'RAILLINE_NAME']
    source_node = rails.at[from_rail, 'FROM_NODE']
    target_node = rails.at[to_rail, 'TO_NODE']

    return calc_point_to_point_travel_time(source_node, target_node)


def calc_point_to_point_travel_time(source_node, target_node, source_node_distance=0, target_node_distance=0):

    shortest_path = nx.shortest_path(G, source=source_node, target=target_node, weight='length')
    travel_time = 0  # time in hours
    current_speed = 0
    shortest_path_edges = zip(shortest_path[:-1], shortest_path[1:])
    shortest_path_dict = {from_node: to_node for from_node, to_node in shortest_path_edges}
    for from_node, to_node in shortest_path_dict.items():
        edge_data = G.get_edge_data(from_node, to_node)
        length = edge_data['length']
        speed_limit = edge_data['speed_limit']

        if to_node == target_node:
            next_speed_limit = 0
        else:
            next_edge_data = G.get_edge_data(to_node, shortest_path_dict[to_node])
            next_speed_limit = next_edge_data['speed_limit']

        travel_time += calculate_edge_travel_time(length, speed_limit, current_speed, next_speed_limit)
        current_speed = next_speed_limit
    return travel_time


rails = pd.read_csv('Rail_ext.csv', delimiter='\t', index_col='NAME')

ports =  pd.read_csv('Port.csv', delimiter='\t', index_col='NAME')
equipment_list = pd.read_csv('Equipment.csv', delimiter='\t', index_col='NAME')
vehicle_types = pd.read_csv('VehicleType.csv', delimiter='\t', index_col='NAME')

# Generate all possible from-to-combinations, excluding combinations with themselves
all_combinations = list(itertools.product(equipment_list.index, repeat=2))
filtered_combinations = [combo for combo in all_combinations if combo[0] != combo[1]]
transport_times = pd.DataFrame(filtered_combinations, columns=['FROM', 'TO'])

G = nx.DiGraph()
def add_to_graph(row):
    from_node = row['FROM_NODE']
    to_node = row['TO_NODE']
    length = row['LENGTH']
    speed_limit=row['MAX_SPEED']
    G.add_edge(from_node, to_node, length=length, speed_limit=speed_limit)  # distance in mm, speed limit in mm/s


rails.apply(add_to_graph, axis=1)

acceleration = vehicle_types['ACCELERATION'][0]
deceleration = vehicle_types['DECELERATION'][0]
max_speed = vehicle_types['MAX_SPEED'][0]


# We divide the route list for faster processing:
start_time = time.time()

def process_chunk(chunk):
    chunk['TRANSPORT_TIME'] = chunk.apply(_calc_travel_time_row, axis=1)
    return chunk

num_splits = cpu_count()
chunks = np.array_split(transport_times, num_splits)
processed_chunks = Parallel(n_jobs=-1)(delayed(process_chunk)(chunk) for chunk in chunks)
transport_times_processed = pd.concat(processed_chunks, ignore_index=True)
end_time = time.time()
duration = end_time - start_time
print(f"...parallelized code completed in {'{:.2f}'.format(duration / 60)} minutes on {num_splits} CPUs. Result has a length of {print(len(transport_times_processed))}")


print(transport_times_processed.info())

transport_times_processed.to_csv('transport_times.csv', sep='\t')
