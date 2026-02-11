# Kill the monster

Kill the Boss is a distributed multiplayer game in which players work together to defeat
recurring bosses by dealing damage and leveling up while contributing.
Players login using a username and join the global game where they deal damage to a
global boss which is shared between all players.

Clients and servers are discovered dynamically.

Bully algorithm is used for leader election between servers.
Reliable ordered multicast with FIFO ordering is used to share damage data.
Gossip is used to share player data between servers.