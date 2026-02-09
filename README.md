# DistributedSystem

## TODOs
#### Server
- [x] Startup
  - [x] Discover leader
    - [x] Broadcast into server network
    - [x] Handle leader answer
    - [x] Start leader election
      - [x] Perform **bully algorithm** 
        - [x] *Check if our modification satisfies failure modes*
- [x] Handle clients
  - [x] Handle client assignment by leader
    - [x] Connect to client
    - [x] Send initial game state
    - [x] Store player in database
      - [x] Perform **gossip algorithm**
  - [x] Aggregate client data (damage dealt)
  - [x] Disconnect timed out clients
- [x] Sync game state
  - [x] Tick system
  - [x] Multicast aggregated data
    - [x] Perform reliable multicast
  - [x] Handle incoming data
  - [x] Handle leader death
  - [ ] *Check which failure modes we want to support*
- [x] Handle leader role
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
- [x] Handle server input
  - [x] Display damage
  - [x] Display/Update boss health
  - [x] Display/Hide win screen
  - [x] Switch server (in case of reassignment)

#### Code Improvements
- [x] Look over Thread creations (performance)
- [ ] Unify sockets that have common attributes / methods
- [x] Switch Broadcast to Multicast (broadcast messages may not be sent to all processes)
- [x] Rethink using standard Bully (higher UUID joins -> triggers election) but current may be better
- [ ] When leader timeouts and comes back after a time 
