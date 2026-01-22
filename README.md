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
  - [ ] Assign new clients
  - [ ] Handle server failure
    - [ ] Reassign orphaned clients

#### Client
- [ ] Startup
  - [x] Login screen
  - [x] Broadcast into server network
  - [x] Display initial game state
    - [x] Display boss health
- [ ] Handle user input
  - [x] Send damage to server
  - [ ] Display damage
  - [x] Disconnect from server
- [ ] Handle server input
  - [ ] Display damage
  - [x] Display/Update boss health
  - [x] Display/Hide win screen
  - [ ] Switch server (in case of reassignment)