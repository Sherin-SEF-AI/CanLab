"""
Example seed-key script for CanLab Security Access.

Drop this file (or a derivative) into any folder, then point
DIAGNOSTICS → SECURITY ACCESS → Script (.py) at it.

The function signature is fixed:
    compute_key(seed: bytes, level: int) -> bytes

seed  : raw bytes received from the ECU (service 0x27, odd subfunction)
level : access level integer (1, 3, 5, …)

Return the key bytes to send back (service 0x27, even subfunction).
"""


def compute_key(seed: bytes, level: int) -> bytes:
    # Replace this body with the actual algorithm for your target ECU.
    # This example: XOR each byte with 0xAA, then add the level.
    return bytes(((b ^ 0xAA) + level) & 0xFF for b in seed)
