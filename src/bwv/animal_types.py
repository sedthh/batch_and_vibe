"""Exact wildlife runtime classes; groups and fishing areas are not individuals."""
ANIMAL_NAMES = ('Animal', 'Cow', 'Sheep', 'Horse', 'Pig', 'Goat', 'Wolf', 'Lion',
                'Tiger', 'Leopard', 'Zebra', 'Tortoise', 'Crow', 'Dove', 'Pigeon')
ANIMAL_TYPES = {f'.?AV{name}@@'.encode(): name.lower() for name in ANIMAL_NAMES}
