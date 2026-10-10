# backend/app/routing/graph.py

airport_graph = {
    "GATE_A1": {
        "T1": 5
    },
    "GATE_B2": {
        "T2": 6
    },
    "T1": {
        "GATE_A1": 5,
        "T2": 4,
        "T3": 7
    },
    "T2": {
        "GATE_B2": 6,
        "T1": 4,
        "T3": 3
    },
    "T3": {
        "T1": 7,
        "T2": 3,
        "RWY_4": 5,
        "RWY_13": 8
    },
    "RWY_4": {
        "T3": 5
    },
    "RWY_13": {
        "T3": 8
    }
}