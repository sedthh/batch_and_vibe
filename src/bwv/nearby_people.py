"""Public person metadata and distinct overview counts."""

# Read-only interoperability state IDs. Labels describe the current phase, not
# a completed outcome or an inferred motive. Unmapped states remain unknown.
ACTIVITIES = {
    1:'walking_to_position', 2:'walking_to_object', 3:'moving_on_structure',
    4:'scripted_actor', 5:'dancing', 6:'fleeing', 7:'looking_at_object',
    8:'following_object', 9:'inspecting_object', 10:'flying', 11:'landed',
    12:'watching_flying_object', 13:'starting_to_die', 14:'dying', 15:'dead',
    16:'drowning', 17:'downed', 18:'being_eaten', 19:'going_to_food',
    20:'arriving_at_food', 21:'going_to_wood', 22:'arriving_at_wood',
    23:'playing_animation', 24:'being_held', 25:'going_to_ball',
    26:'arriving_at_ball', 27:'moving_with_group', 28:'following_path',
    29:'following_path', 30:'fleeing', 31:'taking_resources_to_storage',
    32:'arriving_at_storage_to_drop_resources', 33:'going_to_storage_for_food',
    34:'arriving_at_storage_for_food', 35:'arriving_home_with_food',
    36:'going_home', 37:'arriving_home', 38:'at_home',
    39:'arriving_at_storage_for_building_materials', 40:'arriving_at_building_site',
    41:'building', 42:'fetching_worship_supplies', 43:'collecting_worship_supplies',
    44:'taking_supplies_to_worship_site', 45:'taking_supplies_to_worship_site',
    46:'arriving_with_worship_supplies', 47:'going_to_forest', 48:'going_to_forest',
    49:'arriving_at_forest', 50:'chopping_tree', 51:'chopping_tree_for_building',
    52:'finished_forestry', 53:'arriving_at_forest', 54:'arriving_at_forest_for_building',
    55:'arriving_at_fishing_spot', 56:'fishing', 57:'waiting',
    58:'going_to_worship', 59:'arriving_to_worship', 60:'worshipping',
    61:'going_to_rest_at_altar', 62:'arriving_to_rest_at_altar', 63:'resting_at_altar',
    64:'finishing_rest', 65:'resuming_worship', 66:'resuming_creature_worship',
    67:'resuming_meeting', 68:'arriving_at_field', 69:'planting_crops',
    70:'harvesting_crops', 86:'newly_created', 87:'arriving_to_trade',
    88:'collecting_household_surplus', 89:'becoming_scared', 90:'scared_stiff',
    91:'worshipping_creature', 114:'at_nursery', 115:'following_mother',
    116:'becoming_adult', 117:'sitting_down_to_eat', 118:'eating',
    119:'eating_at_home', 120:'going_to_bed', 121:'sleeping_at_home',
    122:'waking_at_home', 129:'waiting_for_dinner', 130:'homeless',
    131:'wandering_without_home', 132:'mourning', 164:'deciding_what_to_do',
    165:'choosing_interaction', 166:'eating_outside', 167:'going_to_burning_building',
    168:'arriving_at_burning_building', 169:'repairing_building',
    170:'running_away', 171:'approaching_creature', 172:'amazed_by_magic_shield',
    173:'gossiping', 186:'going_to_collect_wood', 187:'collecting_wood',
    188:'resuming_building', 189:'arriving_to_push_object', 190:'taking_wood_from_tree',
    191:'taking_wood_from_pile', 192:'taking_wood_for_building',
    193:'taking_wood_for_building', 194:'taking_animal_for_slaughter',
    195:'taking_charge_of_flock', 196:'releasing_flock', 197:'dancing',
    198:'fainting', 199:'becoming_confused', 200:'confused',
    202:'weak_on_ground', 203:'scripted_wandering', 204:'scripted_actor',
    205:'approaching_teleport', 206:'teleporting', 207:'dancing',
    208:'controlled_by_creature', 209:'pointing_at_dead_person',
    210:'approaching_dead_person', 211:'looking_at_dead_person',
    212:'mourning_dead_person', 213:'idle', 214:'delivering_to_workshop',
    215:'collecting_workshop_materials', 216:'arriving_to_pick_up_scaffolding',
    217:'arriving_with_scaffolding', 218:'moving_scaffolding', 219:'showing_poisoning',
    220:'hiding_at_worship_site', 221:'in_crowd', 222:'reacting_to_fire',
    223:'beating_out_fire', 224:'putting_out_fire_with_water',
    225:'fetching_water_for_fire', 226:'on_fire', 227:'moving_around_fire',
    228:'idle_disciple', 229:'going_to_football', 230:'collecting_goods_for_trade',
    231:'delivering_traded_goods', 232:'working_as_breeder',
    233:'working_as_missionary', 234:'responding_to_breeder',
    235:'checking_animal_for_slaughter', 236:'choosing_interaction',
    237:'dancing_at_artifact', 238:'fleeing_predator', 239:'waiting_for_wood',
    240:'inspecting_object', 241:'going_home_to_change', 242:'waiting_for_mate',
    243:'hiding_in_building', 244:'checking_if_safe', 245:'sleeping_in_tent',
    246:'pausing', 247:'panicking', 248:'getting_food_at_worship_site',
    249:'going_to_town_after_emergency', 250:'gathering_after_emergency',
    251:'scripted_crowd', 252:'going_to_relax_outside_home', 253:'sitting_and_relaxing',
    254:'following_scripted_path',
}
# Only assignments confirmed independently of the current activity are named.
ROLES = {0:'unassigned', 1:'farmer', 2:'forester', 3:'fisher',
         4:'builder', 5:'breeder', 8:'craftsman', 9:'trader', 11:'worshipper'}


def person_details(value):
    if not isinstance(value, dict):return {}
    result = {}
    for key in ('is_actor', 'script_controlled'):
        if type(value.get(key)) is bool:result[key] = value[key]
    if value.get('sex') in ('male', 'female'):result['sex'] = value['sex']
    age = value.get('age_years')
    if type(age) is int and 0 <= age <= 10000:result['age_years'] = age
    if value.get('assigned_role') in ROLES.values():result['assigned_role'] = value['assigned_role']
    if value.get('current_activity') in ACTIVITIES.values():result['current_activity'] = value['current_activity']
    if value.get('life_status') in ('dying', 'dead'):result['life_status'] = value['life_status']
    return result


def overview_kind(obj):
    if obj.kind != 'villager':return obj.kind
    details = person_details(obj.person)
    if details.get('life_status') == 'dead':return 'dead_person'
    if details.get('is_actor') is True:return 'actor'
    return 'villager'
