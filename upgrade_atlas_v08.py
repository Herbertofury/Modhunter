"""Give each Atlas region an authored eight-part campaign and preserve selected navigation."""
from pathlib import Path
p=Path(__file__).resolve().parent/'mo2_update_arsenal/web/siege.js'
s=p.read_text(encoding='utf-8')
if 'const REGION_SAGAS=' in s:
    raise SystemExit('already applied')
sagas=[
['The Last Bell of Winter','Souls Beneath the Ice','The Three Frozen Watchers','Graves of the White Legion','Winterthorn Oathkeepers','The Blizzard Crown','The First Frozen King','Wake the Heart of Winter'],
['The Ashen Convoy','Cinders of the Lost Forge','The Tempered Three','March of the Emberguard','Fury of a Thousand Hammers','The Smelter Ascendant','The Two Molten Thrones','Crown of the Cinder Eye'],
['Whispers in the Reeds','A Harvest of Moon Tears','Guardians of the Sunken Gate','The Pale King’s Muster','The Moonlit Covenant','Under the Drowning Stars','Twin Courts of Night','The Fen Remembers'],
['A Road Through Thorns','A Tithe of Venom','The Three Briarbound','The Thornward Rebellion','The Oath of the Green Crown','Queen of the Poisoned Bough','The Hollow Twins','The Last Root of Blackbriar'],
['Before the First Thunder','Storm-Souls at Dusk','Three Above the Clouds','The Skybreaker Battalion','The Vow of the Thunder-Sworn','Ascend the Struck Spire','The Twin Tempests','Heart of the Stormspire'],
['The Fallen Banner','Scars in the Black Sand','The Red Company','A Legion Without Graves','Oath Beneath the Ashes','The General’s Trial','The Burning Twins','The Last Ember Emperor'],
['The First Flooded Folio','Pearls of the Dead','Three of the Deep','The Librarians Rise','Oaths Written in Salt','The Siltglass Codex','Kings of the Two Tides','The Last Book of Water'],
['The Mountain’s Call','Ironshards of the Deep','Three Forge Guardians','Hammers in the Dark','The Oath of the Ironseed','The Buried Machine','Twin Anvils of Ruin','Awaken the World Forge'],
['The Garden of Forgetting','Blooms of the Silent Dead','The Three Weeping Trees','The Thorned Procession','A Rose for the Fallen','The Immortal Gardener','The Blooming Twins','Reclaim the Living Blossom'],
['The First Black Coin','Souls for Sale','The Three Masked Merchants','Siege of the Shadow Market','The Oath of a Thief','The Guildmaster’s Bargain','The Two Gilded Judges','Break the Obsidian Exchange'],
['At the Edge of Yesterday','Shards of What Might Be','Three Mirror Heroes','Army of Unmade Futures','The Oath Beyond Time','The Broken Astral Gate','The Two Unmoored Selves','Seal the Ivory Rift'],
['Before the Falling Star','Gather the Dawn Embers','The Final Three','March of the Unfinished','The World-Eater’s Oath','Ascend the Starfall Stair','The Twin Sovereigns','Dawn of a Thousand Worlds']]
assert len(sagas)==12 and all(len(x)==8 for x in sagas)
import json
snippet='const REGION_SAGAS='+json.dumps(sagas,ensure_ascii=False,separators=(',',':'))+';\n'
s=s.replace('const CONTRACT_PATTERNS=[',snippet+'const CONTRACT_PATTERNS=[',1)
s=s.replace('${escapeHTML(c.title)}</b><small>${escapeHTML(c.desc)}', '${escapeHTML(REGION_SAGAS[current.region][i])}</b><small>${escapeHTML(c.desc)}',1)
s=s.replace('`${cn.title}: ${cn.desc} Survive ${cn.minutes} minutes', '`${REGION_SAGAS[current.region][current.contract]}: ${cn.desc} Survive ${cn.minutes} minutes',1)
s=s.replace('mission:entry.id};r.arena=realm.name;', 'mission:entry.id,chapter:REGION_SAGAS[region][contract]};r.arena=realm.name;',1)
s=s.replace('`${realm.icon} ${realm.name.toUpperCase()} · ${entry.title}`', '`${realm.icon} ${realm.name.toUpperCase()} · ${REGION_SAGAS[region][contract]}`',1)
s=s.replace('`${contractAt(r).title} · ${a.label}', '`${r.atlas.chapter||contractAt(r).title} · ${a.label}',1)
s=s.replace('region:Number(loc.dataset.atlasRegion),contract:0};renderAtlas();return', 'region:Number(loc.dataset.atlasRegion),contract:0};dirty=true;renderAtlas();void save();return',1)
s=s.replace('contract:Number(chapter.dataset.atlasContract)};renderAtlas();return', 'contract:Number(chapter.dataset.atlasContract)};dirty=true;renderAtlas();void save();return',1)
s=s.replace('tier:Number(e.target.value),contract:0};renderAtlas()', 'tier:Number(e.target.value),contract:0};dirty=true;renderAtlas();void save()',1)
s=s.replace('regions:REGIONS,patterns:CONTRACT_PATTERNS,tiers:MASTERIES,meta:state.atlas','regions:REGIONS,patterns:CONTRACT_PATTERNS,sagas:REGION_SAGAS,tiers:MASTERIES,meta:state.atlas',1)
p.write_text(s,encoding='utf-8')
print('Authored 96 mission chapters',p.stat().st_size)
