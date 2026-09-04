import pandas as pd
import math

rails = pd.read_csv('Rail.csv', delimiter='\t', index_col='NAME')
nodes = pd.read_csv('Adress.csv', delimiter='\t', index_col='NAME')

max_speed_curve = 1000
max_speed_straight = 5000

def calc_length(row):
    from_node = row['FROM_NODE']
    to_node = row['TO_NODE']
    is_curve = row['CURVE']

    from_node_x = float(nodes.at[from_node, 'POSITION_X'])
    from_node_y = float(nodes.at[from_node, 'POSITION_Y'])
    to_node_x = float(nodes.at[to_node, 'POSITION_X'])
    to_node_y = float(nodes.at[to_node, 'POSITION_Y'])

    if is_curve:
        # curves should be treated differently, but as we don't have information on the angle of the incoming and outgoing rails (if they are curves), we continue as if they were straight rails
        pass

    return math.sqrt((to_node_x - from_node_x) ** 2 + (to_node_y - from_node_y) ** 2)

rails['MAX_SPEED'] = rails['CURVE'].map({1: max_speed_curve, 0: max_speed_straight})
rails['CURVE'] = rails['CURVE'].map({1: True, 0: False})
rails['LENGTH'] = rails.apply(calc_length, axis=1)

# sanity check
ports =  pd.read_csv('Port.csv', delimiter='\t', index_col='NAME')

def check_length(row):
    rail = row['RAILLINE_NAME']
    distance = row['DISTANCE']
    rail_lenght = rails.at[rail, 'LENGTH']

    if distance > rail_lenght:
        raise ValueError(f'{row.name} is at distance {distance} on rail {rail}, past its length of {rail_lenght}')

ports.apply(check_length, axis=1)


rails.to_csv('Rail_ext.csv', sep='\t')