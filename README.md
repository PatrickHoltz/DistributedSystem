# DistributedSystem

## TODOs
#### Server
- [ ] Startup
  - [ ] Discover leader
    - [x] Broadcast into server network
    - [ ] Handle leader answer
    - [ ] Start leader election
      - [ ] Perform **bully algorithm** 
        - [ ] *Check if our modification satisfies failure modes*
- [ ] Handle clients
  - [ ] Handle client assignment by leader
    - [x] Connect to client
    - [x] Send initial game state
    - [ ] Store player in database
      - [ ] Perform **gossip algorithm**
  - [ ] Aggregate client data (damage dealt)
  - [x] Disconnect timed out clients
- [ ] Sync game state
  - [ ] Tick system
  - [ ] Multicast aggregated data
    - [ ] Perform reliable multicast
  - [ ] Handle incoming data
  - [ ] Handle leader death
  - [ ] *Check which failure modes we want to support*
- [ ] Handle leader role
  - [x] Assign new clients
  - [x] Handle server failure
    - [x] Reassign orphaned clients (clients reconnect by themselves)

#### Client
- [x] Startup
  - [x] Login screen
  - [x] Broadcast into server network
  - [x] Display initial game state
    - [x] Display boss health
- [x] Handle user input
  - [x] Send damage to server
  - [x] Display damage
  - [x] Disconnect from server
- [ ] Handle server input
  - [x] Display damage
  - [x] Display/Update boss health
  - [x] Display/Hide win screen
  - [x] Switch server (in case of reassignment)

#### Code Improvements
- [ ] Look over Thread creations (performance)
- [ ] Unify sockets that have common attributes / methods
- [ ] Switch Broadcast to Multicast (broadcast messages may not be sent to all processes)
- [ ] Rethink using standard Bully (higher UUID joins -> triggers election) but current may be better
