# How to Build WiP

## Easy: Using GitHub actions locally
### Install [nektos/act](https://github.com/nektos/act) to run GitHub actions locally:   
   
**on macOS via homebrew**  
```
 brew install act
```

Act is using docker under the hood. Make sure its up and running on your machine.   
[How to install docker](https://www.docker.com/get-started)


### Run the dev or release build with Act
```
act -j dev-build -b ./build
act -j release-build -b ./build
```
Thats it. This may take a while but you should now have a **firmware.uf2** and **firmware-blank.uf2** in your build directory.

