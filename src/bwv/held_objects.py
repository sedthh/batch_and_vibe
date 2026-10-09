"""Readable categories for exact runtime object classes, not inferred edibility."""
from .animal_types import ANIMAL_TYPES
HELD_KINDS = ('food_pile', 'tree', 'rock', 'object', 'villager', 'animal',
              'miracle_bubble', 'toy', 'wood_pile', 'mushroom')
RUNTIME_HELD_KINDS = {
    b'.?AVPileFood@@': 'food_pile', b'.?AVTree@@': 'tree', b'.?AVRock@@': 'rock',
    b'.?AVVillager@@': 'villager', b'.?AVSpecialVillager@@': 'villager',
    b'.?AVAnimal@@': 'animal', b'.?AVCow@@': 'animal', b'.?AVSheep@@': 'animal',
    b'.?AVHorse@@': 'animal', b'.?AVOneOffSpellSeed@@': 'miracle_bubble',
    b'.?AVSpellSeed@@': 'miracle_bubble', b'.?AVBall@@': 'toy', b'.?AVFootball@@': 'toy',
    b'.?AVPileWood@@': 'wood_pile',
    **{name: 'animal' for name in ANIMAL_TYPES},
}
RUNTIME_HELD_DETAILS = {b'.?AVCow@@':'cow', b'.?AVSheep@@':'sheep',
    b'.?AVHorse@@':'horse', b'.?AVBall@@':'ball', b'.?AVFootball@@':'football',
    **{name: detail for name, detail in ANIMAL_TYPES.items() if detail != 'animal'}}
HELD_LABELS = {'food_pile':'a food pile', 'tree':'a tree', 'rock':'a rock',
    'object':'an unidentified object', 'villager':'a villager', 'animal':'an animal',
    'miracle_bubble':'a miracle bubble', 'toy':'a toy', 'wood_pile':'a wood pile', 'mushroom':'a mushroom'}
